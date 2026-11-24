from __future__ import annotations

import asyncio
import logging

from copyflow_core.executors.base import Position
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ExecutionMode, Fill, ReconEvent, Subscription

log = logging.getLogger(__name__)

QTY_TOL = 1e-9


async def expected_positions(session: AsyncSession, subscription_id: int) -> dict[str, Position]:
    """Rebuild positions from the local fills ledger (net buy - sell per symbol)."""
    fills = (
        await session.execute(
            select(Fill).where(Fill.subscription_id == subscription_id).order_by(Fill.id)
        )
    ).scalars().all()
    net_qty: dict[str, float] = {}
    entry_pv: dict[str, float] = {}
    for f in fills:
        signed = f.qty if f.side == "buy" else -f.qty
        prev = net_qty.get(f.symbol, 0.0)
        new = prev + signed
        if abs(prev) < QTY_TOL or (prev > 0) != (new > 0):
            # opening or flipping: reset entry basis
            entry_pv[f.symbol] = f.price * abs(new) if abs(new) > QTY_TOL else 0.0
        else:
            entry_pv[f.symbol] = entry_pv.get(f.symbol, 0.0) + f.price * abs(signed)
        net_qty[f.symbol] = new

    out: dict[str, Position] = {}
    for sym, net in net_qty.items():
        if abs(net) < QTY_TOL:
            continue
        avg_entry = (entry_pv.get(sym, 0.0) / abs(net)) if net else 0.0
        out[sym] = Position(
            symbol=sym, side="long" if net > 0 else "short", qty=abs(net), entry_price=avg_entry
        )
    return out


class Reconciler:
    """Periodically compares exchange positions with the local ledger for live subs."""

    def __init__(self, session_factory, executor_factory, delivery=None,
                 notifier=None, interval: float = 30.0):
        self._session_factory = session_factory
        self._factory = executor_factory
        self._delivery = delivery
        self._notifier = notifier
        self._interval = interval
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="reconciler")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _run(self) -> None:
        while True:
            try:
                await self.reconcile_once()
            except Exception:
                log.exception("reconcile pass failed")
            await asyncio.sleep(self._interval)

    async def reconcile_once(self) -> list[dict]:
        from app.db.models import User

        events: list[dict] = []
        async with self._session_factory() as session:  # type: AsyncSession
            subs = (
                await session.execute(
                    select(Subscription).where(
                        Subscription.mode == ExecutionMode.LIVE,
                        Subscription.is_active.is_(True),
                    )
                )
            ).scalars().all()

            for sub in subs:
                user = await session.get(User, sub.user_id)
                try:
                    adapter = await self._factory.get(sub)
                    actual = await adapter.positions()
                except Exception as e:
                    log.error("reconcile: cannot read exchange state sub=%s: %s", sub.id, e)
                    continue

                expected = await expected_positions(session, sub.id)
                mismatch = self._diff(expected, {p.symbol: p for p in actual})
                if not mismatch:
                    continue
                detail = {"subscription_id": sub.id, "user": user.display_name if user else str(sub.user_id), **mismatch}
                session.add(ReconEvent(subscription_id=sub.id, detail=detail))
                events.append(detail)
                log.error("RECON MISMATCH sub=%s: %s", sub.id, detail)
                if self._delivery:
                    await self._delivery.send_risk(
                        session, sub,
                        {"type": "risk", "subscription_id": sub.id,
                         "reason": f"recon mismatch: {mismatch['problem']}", "detail": detail},
                    )
                if self._notifier:
                    await self._notifier.notify(
                        f"⚠️ RECON MISMATCH sub={sub.id}\n{mismatch['problem']}"
                    )
            await session.commit()
        return events

    @staticmethod
    def _diff(expected: dict[str, Position], actual: dict[str, Position]) -> dict:
        problems: list[str] = []
        for sym, exp in expected.items():
            act = actual.get(sym)
            if act is None:
                problems.append(f"{sym}: ledger {exp.side} {exp.qty:.8f} but exchange flat")
            elif act.side != exp.side:
                problems.append(f"{sym}: ledger {exp.side} vs exchange {act.side}")
            elif abs(act.qty - exp.qty) > max(QTY_TOL, exp.qty * 1e-6):
                problems.append(
                    f"{sym}: qty ledger {exp.qty:.8f} vs exchange {act.qty:.8f} ({act.side})"
                )
        for sym, act in actual.items():
            if sym not in expected:
                problems.append(f"{sym}: exchange {act.side} {act.qty:.8f} but ledger flat")
        if not problems:
            return {}
        return {"problem": "; ".join(problems)}
