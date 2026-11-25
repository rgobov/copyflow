from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import ExecutionMode, LedgerEntry, LedgerKind, Subscription

log = logging.getLogger(__name__)


@dataclass
class FeeSplit:
    total: float
    leader: float
    platform: float
    profit: float
    hwm_before: float


def effective_pcts(sub: Subscription) -> tuple[float, float]:
    """(profit_share_pct, leader_share_pct) with global fallbacks."""
    ps = sub.profit_share_pct if sub.profit_share_pct is not None else settings.billing_profit_share_pct
    ls = sub.leader_share_pct if sub.leader_share_pct is not None else settings.billing_leader_share_pct
    return max(0.0, ps), min(100.0, max(0.0, ls))


async def accrue_fees(session: AsyncSession, sub: Subscription, equity_now: float) -> LedgerEntry | None:
    """Charge profit share when equity makes a new all-time high.

    High-water-mark crystallization: fees accrue only on fresh gains, never
    repeatedly on the same gains, nothing is charged below the previous peak.
    Must be called BEFORE evaluate_risk (it owns the peak_equity update).
    Live mode only — paper subscriptions are virtual.
    """
    hwm_before = sub.peak_equity or 0.0
    if hwm_before <= 0:
        # first equity observation: initialize the high-water mark, never charge
        sub.peak_equity = equity_now
        return None

    if sub.mode != ExecutionMode.LIVE:
        sub.peak_equity = max(hwm_before, equity_now)
        return None

    ps_pct, ls_pct = effective_pcts(sub)
    if ps_pct <= 0:
        sub.peak_equity = max(hwm_before, equity_now)
        return None

    if equity_now <= hwm_before:
        sub.peak_equity = max(hwm_before, equity_now)
        return None  # no new high -> nothing crystallized

    profit = equity_now - hwm_before
    total = round(profit * ps_pct / 100.0, 4)
    if total <= 0:
        sub.peak_equity = equity_now
        return None
    leader_cut = round(total * ls_pct / 100.0, 4)
    platform_cut = round(total - leader_cut, 4)

    entry = LedgerEntry(
        subscription_id=sub.id,
        leader_id=sub.leader_id,
        kind=LedgerKind.PLATFORM_FEE,
        amount_usd=platform_cut,
        profit_usd=round(profit, 4),
        equity_usd=equity_now,
        hwm_before=hwm_before,
        note=f"profit share {ps_pct}% on +{profit:.2f} (hwm {hwm_before:.2f}->{equity_now:.2f})",
    )
    session.add(entry)
    if leader_cut > 0:
        session.add(LedgerEntry(
            subscription_id=sub.id,
            leader_id=sub.leader_id,
            kind=LedgerKind.LEADER_COMMISSION,
            amount_usd=leader_cut,
            profit_usd=round(profit, 4),
            equity_usd=equity_now,
            hwm_before=hwm_before,
            note=f"leader commission {ls_pct:.0f}% of {total:.2f}",
        ))
    sub.peak_equity = equity_now
    log.info("billing: sub=%s crystallized +%.2f -> fee %.4f (leader %.4f)",
             sub.id, profit, platform_cut, leader_cut)
    return entry
