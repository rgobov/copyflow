from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AccountSnapshot, DailyState, Subscription

log = logging.getLogger(__name__)


@dataclass
class RiskState:
    """Read-only risk assessment. The server never mutates subscription state:
    breach -> delivery of a risk event, the client decides what to do."""

    day_pnl_pct: float
    drawdown_pct: float
    daily_breach: bool
    drawdown_breach: bool

    @property
    def breached(self) -> bool:
        return self.daily_breach or self.drawdown_breach

    @property
    def reason(self) -> str:
        parts = []
        if self.daily_breach:
            parts.append(f"daily loss {self.day_pnl_pct:.1f}%")
        if self.drawdown_breach:
            parts.append(f"drawdown {self.drawdown_pct:.1f}%")
        return "; ".join(parts)


async def ensure_daily(session: AsyncSession, sub: Subscription, equity_now: float) -> DailyState:
    """Get or create today's risk row."""
    today = datetime.now(UTC).date()
    row = (
        await session.execute(
            select(DailyState).where(
                DailyState.subscription_id == sub.id, DailyState.day == today
            )
        )
    ).scalar_one_or_none()
    if row is None:
        row = DailyState(
            subscription_id=sub.id, day=today,
            start_equity=equity_now, peak_equity=equity_now,
        )
        session.add(row)
        await session.flush()
    else:
        row.peak_equity = max(row.peak_equity or 0.0, equity_now)
    return row


async def evaluate_risk(session: AsyncSession, sub: Subscription, equity_now: float) -> tuple[DailyState, RiskState]:
    """Compute daily pnl / drawdown, update peak, write an equity snapshot.

    Returns (today's risk row, assessment) so callers can do edge-detection
    on notification flags.
    """
    daily = await ensure_daily(session, sub, equity_now)

    day_pnl_pct = 0.0
    if daily.start_equity > 0:
        day_pnl_pct = (equity_now - daily.start_equity) / daily.start_equity * 100.0
    drawdown_pct = 0.0
    if sub.peak_equity and sub.peak_equity > 0:
        drawdown_pct = (sub.peak_equity - equity_now) / sub.peak_equity * 100.0

    sub.peak_equity = max(sub.peak_equity or 0.0, equity_now)

    daily_breach = day_pnl_pct <= -sub.daily_loss_limit_pct
    drawdown_breach = sub.max_drawdown_pct > 0 and drawdown_pct >= sub.max_drawdown_pct

    session.add(AccountSnapshot(subscription_id=sub.id, equity_usd=equity_now))
    return daily, RiskState(day_pnl_pct, drawdown_pct, daily_breach, drawdown_breach)


async def reset_notification_flags(daily: DailyState, state: RiskState) -> None:
    """Edge-detection bookkeeping: a breach pushes once per episode."""
    if not state.daily_breach:
        daily.daily_loss_notified = False
    if not state.drawdown_breach:
        daily.drawdown_notified = False
