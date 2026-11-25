from __future__ import annotations

import asyncio
import hashlib
import json
import logging

from copyflow_core.executors.base import ExecutionAdapter, ExecutionError
from copyflow_core.executors.binance import BinanceExecutor
from copyflow_core.executors.hyperliquid import HyperliquidExecutor
from copyflow_core.executors.paper import PaperConfig, PaperExecutor
from copyflow_core.models import Action, SignalEvent
from copyflow_core.sizing import CopyLimits, SkipSignal, compute_size, resolve_action
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.bus import EventBus
from app.db.models import Fill, Order, OrderStatus, Signal, Subscription, User
from app.engine.risk import evaluate_risk

log = logging.getLogger(__name__)


class ExecutorFactory:
    """Builds/caches one adapter per subscription.

    Live credentials are taken from the subscription's encrypted payload
    ({"api_key","api_secret"} for binance, {"private_key","address"} for
    hyperliquid) and fall back to global settings when absent.
    """

    def __init__(self, session_factory=None) -> None:
        self._cache: dict[int, ExecutionAdapter] = {}
        self._session_factory = session_factory

    def _credentials(self, sub: Subscription) -> dict:
        if not sub.encrypted_credentials:
            return {}
        from app.security import decrypt

        try:
            return json.loads(decrypt(sub.encrypted_credentials))
        except Exception as e:
            raise ExecutionError(f"sub={sub.id}: cannot decrypt credentials: {e}") from e

    async def get(self, sub: Subscription) -> ExecutionAdapter:
        if sub.id in self._cache:
            return self._cache[sub.id]
        creds = self._credentials(sub)
        kind = getattr(sub.executor, "value", str(sub.executor))
        if kind == "paper":
            adapter: ExecutionAdapter = PaperExecutor(
                PaperConfig(starting_equity_usd=sub.equity_usd)
            )
        elif kind == "hyperliquid":
            adapter = HyperliquidExecutor(
                api_url=creds.get("api_url", settings.hyperliquid_api_url),
                agent_private_key=creds.get("private_key", settings.hl_agent_private_key),
                agent_address=creds.get("address", settings.hl_agent_address),
                builder_code=creds.get("builder_code", settings.hl_builder_code) or None,
            )
        elif kind == "binance":
            adapter = BinanceExecutor(
                api_key=creds.get("api_key", settings.binance_api_key),
                api_secret=creds.get("api_secret", settings.binance_api_secret),
                testnet=creds.get("testnet", settings.binance_testnet),
            )
        else:
            raise ExecutionError(f"unknown executor {sub.executor}")
        self._cache[sub.id] = adapter
        return adapter

    def reset(self, subscription_id: int) -> None:
        self._cache.pop(subscription_id, None)


#: Shared adapter cache: paper state lives in adapter instances, so everyone
#: (copy engine, API, bot) must use the same factory.
default_executor_factory = ExecutorFactory()


class CopyEngine:
    def __init__(self, bus: EventBus, session_factory, executor_factory: ExecutorFactory | None = None,
                 delivery=None):
        self._bus = bus
        self._session_factory = session_factory
        self._factory = executor_factory or default_executor_factory
        from app.delivery.channels import DeliveryManager

        self._delivery = delivery or DeliveryManager()
        self._task: asyncio.Task | None = None

    # ---- lifecycle ----
    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="copy-engine")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _run(self) -> None:
        async for event in self._bus.subscribe(subscriber_id=0):
            if event is None:
                return
            try:
                await self.handle_signal(event)
            except Exception:
                log.exception("engine failed on signal %s", event.id)

    # ---- core ----
    async def handle_signal(self, event: SignalEvent) -> None:
        async with self._session_factory() as session:  # type: AsyncSession
            subs = (
                await session.execute(
                    select(Subscription).where(
                        Subscription.leader_id == event.leader_id,
                        Subscription.is_active.is_(True),
                    )
                )
            ).scalars().all()

            # persist signal once (idempotent)
            exists = await session.get(Signal, event.id)
            if exists is None:
                session.add(Signal(id=event.id, leader_id=event.leader_id, payload=event.model_dump(mode="json")))
                await session.commit()

            # leader's virtual multi-market portfolio
            from sqlalchemy import select as _sel

            from app.db.models import Leader as _Leader
            from app.engine.leader_portfolio import apply_leader_signal

            _leader = (await session.execute(
                _sel(_Leader).where(_Leader.id == event.leader_id)
            )).scalar_one_or_none()
            if _leader is not None:
                await apply_leader_signal(
                    session, event, float(_leader.equity_usd or 10_000.0)
                )
                await session.commit()

            for sub in subs:
                try:
                    await self._process_subscription(session, sub, event)
                except Exception:
                    log.exception(
                        "subscription %s failed on signal %s", sub.id, event.id
                    )

    async def _process_subscription(
        self, session: AsyncSession, sub: Subscription, event: SignalEvent
    ) -> None:
        client_order_id = self.client_order_id(event.id, sub.id)
        dup = await session.execute(
            select(Order).where(Order.client_order_id == client_order_id)
        )
        if dup.scalar_one_or_none() is not None:
            return  # idempotency

        # ---- delivery mode: push to client app, client executes itself ----
        if sub.delivery != "auto":
            leader_name = await self._leader_name(session, sub)
            leader_equity = await self._leader_equity(session, sub, event)
            payload = {
                "type": "signal",
                "signal_id": event.id,
                "leader_name": leader_name,
                "leader_id": sub.leader_id,
                "subscription_id": sub.id,
                "leader_equity_usd": leader_equity,
                "signal": event.model_dump(mode="json"),
                "risk": {
                    "daily_loss_limit_pct": sub.daily_loss_limit_pct,
                    "max_drawdown_pct": sub.max_drawdown_pct,
                    "peak_equity": sub.peak_equity,
                },
            }
            await self._delivery.send_signal(session, sub, event.id, payload)
            await session.commit()
            return

        adapter = await self._factory.get(sub)
        limits = CopyLimits(
            sizing=sub.sizing,
            fixed_usd=sub.fixed_usd,
            equity_usd=sub.equity_usd,
            leverage_cap=sub.leverage_cap,
            max_price_deviation_pct=sub.max_price_deviation_pct,
            slippage_bps=sub.slippage_bps,
            min_notional_usd=sub.min_notional_usd,
            max_positions=sub.max_positions,
            blacklisted_symbols=sub.blacklisted_symbols or [],
            daily_loss_limit_pct=sub.daily_loss_limit_pct,
            max_drawdown_pct=sub.max_drawdown_pct,
        )

        symbol = event.instrument.symbol
        positions = await adapter.positions()
        current_pos = next((p for p in positions if p.symbol == symbol), None)

        ref_price = await adapter.mark_price(symbol) or event.ref_price

        # ---- follower portfolio allocation (strict) ----
        user = await session.get(User, sub.user_id)
        effective_equity = equity_now = (await adapter.account_state()).equity_usd
        if user and user.total_capital_usd > 0:
            effective_equity = user.total_capital_usd * (sub.allocation_pct or 0.0) / 100.0
            if effective_equity <= 0:
                self._record_skip(session, sub, event, client_order_id, symbol,
                                  "no allocation (set allocation_pct > 0)")
                await session.commit()
                return

        # ---- risk: detect + snapshot (no server-side pause; client decides) ----
        await evaluate_risk(session, sub, equity_now)
        from app.engine.billing import accrue_fees

        await accrue_fees(session, sub, equity_now)

        leader_equity = await self._leader_equity(session, sub, event)

        try:
            action = resolve_action(event, current_pos)
            if action == Action.CLOSE and (current_pos is None or current_pos.qty <= 0):
                self._record_skip(session, sub, event, client_order_id, symbol, "nothing to close")
                return
            decision = compute_size(
                event=event,
                action=action,
                limits=limits,
                leader_equity_usd=leader_equity,
                follower_positions=positions,
                ref_price=ref_price or event.ref_price or 0.0,
                current_pos=current_pos,
                equity_now_usd=effective_equity,
            )
        except SkipSignal as e:
            self._record_skip(session, sub, event, client_order_id, symbol, e.reason)
            await session.commit()
            return

        # flip = close existing position first, then open the new side
        if action == Action.FLIP and current_pos is not None:
            close_notional = current_pos.qty * (ref_price or current_pos.entry_price)
            close_side = "sell" if current_pos.side == "long" else "buy"
            try:
                await adapter.market_order(
                    symbol, close_side, close_notional,
                    client_order_id + "-c", reduce_only=True,
                    ref_price=ref_price, slippage_bps=limits.slippage_bps,
                )
            except ExecutionError as e:
                log.error("flip close failed sub=%s: %s", sub.id, e)

        order = Order(
            client_order_id=client_order_id,
            signal_id=event.id,
            subscription_id=sub.id,
            venue=adapter.venue,
            symbol=symbol,
            side=decision.side,
            kind=action.value,
            notional_usd=decision.notional_usd,
            status=OrderStatus.NEW,
        )
        session.add(order)
        await session.flush()

        try:
            fill = await adapter.market_order(
                symbol=symbol,
                side=decision.side,
                notional_usd=decision.notional_usd,
                client_order_id=client_order_id,
                reduce_only=decision.reduce_only,
                ref_price=ref_price,
                slippage_bps=limits.slippage_bps,
            )
        except ExecutionError as e:
            order.status = OrderStatus.FAILED
            order.skip_reason = str(e)[:250]
            log.error("execution failed sub=%s: %s", sub.id, e)
        else:
            order.status = OrderStatus.FILLED
            session.add(
                Fill(
                    order_id=order.id,
                    subscription_id=sub.id,
                    symbol=fill.symbol,
                    side=fill.side,
                    qty=fill.qty,
                    price=fill.price,
                    fee_usd=fill.fee_usd,
                )
            )
            log.info(
                "filled %s sub=%s(mode=%s) %s %s notional=%.2f @ %.4f",
                client_order_id, sub.id, sub.mode.value, symbol, decision.side,
                decision.notional_usd, fill.price,
            )
        await session.commit()

    # ---- helpers ----
    async def _leader_equity(self, session: AsyncSession, sub: Subscription, event: SignalEvent) -> float:
        from app.db.models import Leader

        res = await session.execute(select(Leader).where(Leader.id == sub.leader_id))
        leader = res.scalar_one_or_none()
        return float(leader.equity_usd or 10_000.0) if leader else 10_000.0

    async def _leader_name(self, session: AsyncSession, sub: Subscription) -> str:
        from app.db.models import Leader

        leader = await session.get(Leader, sub.leader_id)
        return leader.name if leader else str(sub.leader_id)

    def _record_skip(self, session, sub, event, client_order_id, symbol, reason):
        session.add(
            Order(
                client_order_id=client_order_id,
                signal_id=event.id,
                subscription_id=sub.id,
                venue=sub.executor.value,
                symbol=symbol,
                side=event.side.value,
                kind=event.action.value,
                notional_usd=0.0,
                status=OrderStatus.SKIPPED,
                skip_reason=reason[:250],
            )
        )

    @staticmethod
    def client_order_id(signal_id: str, subscription_id: int) -> str:
        h = hashlib.sha1(f"{signal_id}:{subscription_id}".encode()).hexdigest()[:24]
        return f"cf{subscription_id}{signal_id[:16]}{h}"
