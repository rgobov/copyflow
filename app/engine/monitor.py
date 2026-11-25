from __future__ import annotations

import asyncio
import logging

from copyflow_core.executors.base import ExecutionError
from sqlalchemy import select

from app.db.models import Subscription
from app.engine.risk import evaluate_risk, reset_notification_flags

log = logging.getLogger(__name__)


class RiskMonitor:
    """Background equity watcher for open positions.

    The server never pauses or flattens: when daily-loss or drawdown limits
    are breached (crossed = edge), a risk event is pushed to the client app
    (signed webhook / telegram) and the client decides what to do.
    """

    def __init__(self, session_factory, executor_factory, delivery=None, interval: float = 10.0):
        self._session_factory = session_factory
        self._factory = executor_factory
        self._delivery = delivery
        self._interval = interval
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="risk-monitor")

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
                await self.check_once()
            except Exception:
                log.exception("risk monitor pass failed")
            await asyncio.sleep(self._interval)

    async def check_once(self) -> list[dict]:
        """One pass over active subscriptions. Returns breached subscriptions."""

        breaches: list[dict] = []
        async with self._session_factory() as session:  # type: AsyncSession
            subs = (
                await session.execute(
                    select(Subscription).where(Subscription.is_active.is_(True))
                )
            ).scalars().all()

            for sub in subs:
                try:
                    adapter = await self._factory.get(sub)
                    equity = (await adapter.account_state()).equity_usd
                except ExecutionError as e:
                    log.error("monitor: cannot read account sub=%s: %s", sub.id, e)
                    continue

                daily, risk = await evaluate_risk(session, sub, equity)
                from app.engine.billing import accrue_fees

                await accrue_fees(session, sub, equity)

                # edge detection: push once per breach episode
                notify = (
                    (risk.daily_breach and not daily.daily_loss_notified)
                    or (risk.drawdown_breach and not daily.drawdown_notified)
                )
                if notify:
                    detail = {
                        "subscription_id": sub.id,
                        "equity_usd": equity,
                        "day_pnl_pct": risk.day_pnl_pct,
                        "drawdown_pct": risk.drawdown_pct,
                        "reason": risk.reason,
                    }
                    if risk.daily_breach:
                        daily.daily_loss_notified = True
                    if risk.drawdown_breach:
                        daily.drawdown_notified = True
                    if self._delivery:
                        await self._delivery.send_risk(
                            session, sub,
                            {
                                "type": "risk",
                                "subscription_id": sub.id,
                                "risk": {
                                    "daily_breach": risk.daily_breach,
                                    "drawdown_breach": risk.drawdown_breach,
                                    "day_pnl_pct": risk.day_pnl_pct,
                                    "drawdown_pct": risk.drawdown_pct,
                                    "daily_loss_limit_pct": sub.daily_loss_limit_pct,
                                    "max_drawdown_pct": sub.max_drawdown_pct,
                                },
                                "reason": risk.reason,
                            },
                        )
                    log.error(
                        "RISK BREACH sub=%s equity=%.2f day=%.1f%% dd=%.1f%% (%s)",
                        sub.id, equity, risk.day_pnl_pct, risk.drawdown_pct, risk.reason,
                    )
                    breaches.append(detail)

                await reset_notification_flags(daily, risk)
            await session.commit()
        return breaches
