from __future__ import annotations

import logging

from copyflow_core.models import Action, SignalEvent
from copyflow_core.sizing import resolve_action
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import LeaderPosition

log = logging.getLogger(__name__)


async def apply_leader_signal(session: AsyncSession, event: SignalEvent,
                              leader_equity_usd: float) -> None:
    """Update the leader's virtual multi-market portfolio from their signal.

    The leader 'assembles a portfolio' by sending signals across venues;
    positions are derived from the same sizing semantics followers see
    (frac of leader equity / ref price).
    """

    venue = event.instrument.venue
    symbol = event.instrument.symbol
    price = event.ref_price or 0.0
    if price <= 0:
        log.debug("leader portfolio: no price for %s %s, skipping", venue, symbol)
        return

    pos = (await session.execute(
        select(LeaderPosition).where(
            LeaderPosition.leader_id == event.leader_id,
            LeaderPosition.venue == venue,
            LeaderPosition.symbol == symbol,
        )
    )).scalar_one_or_none()

    current_side = None
    current_qty = 0.0
    if pos is not None and pos.qty > 1e-12:
        current_side = pos.side
        current_qty = pos.qty

    from copyflow_core.executors.base import Position

    action = resolve_action(
        event,
        Position(symbol=symbol, side=current_side, qty=current_qty, entry_price=0)
        if current_side else None,
    )

    signed_qty = event.size_usd(leader_equity_usd) / price

    if action == Action.CLOSE:
        if pos is not None:
            await session.delete(pos)
        return

    if action == Action.REDUCE:
        if pos is None or pos.qty <= 0:
            return
        frac = min(1.0, event.size_frac if event.size_frac is not None else 1.0)
        pos.qty = max(0.0, pos.qty * (1.0 - frac))
        if pos.qty <= 1e-12:
            await session.delete(pos)
        return

    # OPEN / INCREASE / FLIP
    if action == Action.FLIP and pos is not None:
        await session.delete(pos)
        pos = None

    if pos is None:
        market, currency, _lot, _dec = _market_meta(venue, symbol)
        pos = LeaderPosition(
            leader_id=event.leader_id, venue=venue, symbol=symbol,
            market=market, currency=currency, side=event.side.value,
            qty=0.0, entry_price=price,
        )
        session.add(pos)

    pos.side = event.side.value
    pos.qty = pos.qty + signed_qty
    if pos.entry_price <= 0:
        pos.entry_price = price


def _market_meta(venue: str, symbol: str) -> tuple[str, str, float, int]:
    from copyflow_core.instruments import static_spec

    spec = static_spec(venue, symbol)
    if spec:
        return spec.market, spec.currency, spec.lot_size, spec.qty_decimals
    defaults = {
        "moex": ("ru", "RUB", 1.0, 2),
        "us": ("us", "USD", 1.0, 2),
        "india": ("in", "INR", 1.0, 2),
        "nse": ("in", "INR", 1.0, 2),
    }
    if venue == "binance" or symbol.split("-")[0] in (
        "BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA", "AVAX", "LINK", "TON"
    ):
        return "crypto", "USDT", 1e-4, 6
    return defaults.get(venue, ("crypto", "USDT", 1.0, 6))


async def leader_positions(session: AsyncSession, leader_id: int) -> list[LeaderPosition]:
    return list((await session.execute(
        select(LeaderPosition).where(
            LeaderPosition.leader_id == leader_id, LeaderPosition.qty > 1e-12
        )
    )).scalars().all())
