from __future__ import annotations

from copyflow_core.models import Action, Instrument, Side, SignalEvent
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_session
from app.db.models import ExecutionMode, ExecutorKind, Fill, Leader, Subscription, User

router = APIRouter(prefix="/api/v1")


def _md():
    """MultiMarketData with MOEX instrument registry (routing + FX)."""
    from copyflow_core.instruments import InstrumentRegistry
    from copyflow_core.marketdata import MultiMarketData

    from app.config import settings

    registry = InstrumentRegistry(verify_tls=settings.market_tls_verify)
    return MultiMarketData(registry=registry, verify_tls=settings.market_tls_verify)


# ---- auth ----
async def current_user(
    x_api_key: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
) -> User:
    if not x_api_key:
        raise HTTPException(401, "missing X-Api-Key header")
    user = (
        await session.execute(select(User).where(User.api_key == x_api_key))
    ).scalar_one_or_none()
    if not user:
        raise HTTPException(401, "invalid api key")
    return user


# ---- schemas ----
class SignalIn(BaseModel):
    leader_id: int
    venue: str = "paper"
    symbol: str
    action: Action
    side: Side
    size_frac: float | None = Field(None, ge=0, le=10)
    size_abs_usd: float | None = Field(None, gt=0)
    ref_price: float | None = None
    take_profit: float | None = None
    stop_loss: float | None = None


class LeaderIn(BaseModel):
    name: str
    source: str = "strategy"
    config: dict = {}
    equity_usd: float = 10_000.0
    # for cex_trader leaders: read-only keys, encrypted at rest
    api_key: str | None = None
    api_secret: str | None = None


class SubscriptionIn(BaseModel):
    leader_id: int
    executor: ExecutorKind = ExecutorKind.PAPER
    mode: ExecutionMode = ExecutionMode.PAPER
    delivery: str = "auto"  # auto | webhook | telegram
    webhook_url: str | None = None
    webhook_secret: str | None = None
    allocation_pct: float = 0.0
    sizing: str = "equity_frac"
    fixed_usd: float = 0.0
    equity_usd: float = 1_000.0
    leverage_cap: float = 3.0
    max_price_deviation_pct: float = 1.0
    slippage_bps: int = 10
    min_notional_usd: float = 10.0
    max_positions: int = 10
    blacklisted_symbols: list[str] = []
    daily_loss_limit_pct: float = 10.0
    max_drawdown_pct: float = 25.0


class SubscriptionPatch(BaseModel):
    delivery: str | None = None
    webhook_url: str | None = None
    webhook_secret: str | None = None
    allocation_pct: float | None = None
    equity_usd: float | None = None
    leverage_cap: float | None = None
    max_price_deviation_pct: float | None = None
    slippage_bps: int | None = None
    min_notional_usd: float | None = None
    max_positions: int | None = None
    blacklisted_symbols: list[str] | None = None
    daily_loss_limit_pct: float | None = None
    max_drawdown_pct: float | None = None
    is_active: bool | None = None


# ---- health ----
@router.get("/health")
async def health():
    return {"status": "ok"}


@router.get("/me")
async def me(user: User = Depends(current_user)):
    return {
        "id": user.id,
        "display_name": user.display_name,
        "total_capital_usd": user.total_capital_usd,
        "portfolio_currency": user.portfolio_currency,
        "created_at": user.created_at,
    }


class MePatch(BaseModel):
    display_name: str | None = None
    total_capital_usd: float | None = None
    portfolio_currency: str | None = None


@router.patch("/me")
async def patch_me(
    body: MePatch, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)
):
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(user, field, value)
    await session.commit()
    return {"id": user.id, "total_capital_usd": user.total_capital_usd,
            "portfolio_currency": user.portfolio_currency}


# ---- signals feed (leader tape) ----
@router.get("/signals")
async def signals_feed(limit: int = 50, session: AsyncSession = Depends(get_session)):
    from app.db.models import Leader, Signal

    rows = (await session.execute(
        select(Signal, Leader.name)
        .join(Leader, Leader.id == Signal.leader_id)
        .order_by(Signal.created_at.desc())
        .limit(min(limit, 200))
    )).all()
    out = []
    for sig, leader_name in rows:
        p = sig.payload or {}
        inst = p.get("instrument", {})
        out.append({
            "signal_id": sig.id,
            "leader_name": leader_name,
            "leader_id": sig.leader_id,
            "symbol": inst.get("symbol"),
            "venue": inst.get("venue"),
            "action": p.get("action"),
            "side": p.get("side"),
            "size_frac": p.get("size_frac"),
            "size_abs_usd": p.get("size_abs_usd"),
            "ref_price": p.get("ref_price"),
            "ts": sig.created_at,
        })
    return out


# ---- signals webhook (strategies) ----
@router.post("/signals")
async def post_signal(
    body: SignalIn,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    from app.main import bus

    leader = await session.get(Leader, body.leader_id)
    if not leader or not leader.is_active:
        raise HTTPException(404, "leader not found or inactive")

    event = SignalEvent(
        leader_id=body.leader_id,
        instrument=Instrument(venue=body.venue, symbol=body.symbol),
        action=body.action,
        side=body.side,
        size_frac=body.size_frac,
        size_abs_usd=body.size_abs_usd,
        ref_price=body.ref_price,
        take_profit=body.take_profit,
        stop_loss=body.stop_loss,
    )
    await bus.publish(event)
    return {"signal_id": event.id}


# ---- leaders ----
@router.get("/leaders")
async def list_leaders(session: AsyncSession = Depends(get_session)):
    from sqlalchemy import func

    from app.db.models import Signal, Subscription

    leaders = (await session.execute(select(Leader))).scalars().all()
    counts = dict((await session.execute(
        select(Signal.leader_id, func.count(Signal.id)).group_by(Signal.leader_id)
    )).all())
    last = dict((await session.execute(
        select(Signal.leader_id, func.max(Signal.created_at)).group_by(Signal.leader_id)
    )).all())
    subs = dict((await session.execute(
        select(Subscription.leader_id, func.count(Subscription.id)).group_by(Subscription.leader_id)
    )).all())
    return [
        {
            "id": ld.id,
            "name": ld.name,
            "source": ld.source,
            "equity_usd": ld.equity_usd,
            "is_active": ld.is_active,
            "signal_count": counts.get(ld.id, 0),
            "last_signal_at": last.get(ld.id),
            "followers": subs.get(ld.id, 0),
        }
        for ld in leaders
    ]


@router.post("/leaders")
async def create_leader(
    body: LeaderIn, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)
):
    from app.security import encrypt

    config = dict(body.config or {})
    if body.source == "cex_trader":
        if not body.api_key or not body.api_secret:
            raise HTTPException(422, "cex_trader requires read-only api_key and api_secret")
        config["exchange"] = config.get("exchange", "binance")
        config["api_key"] = encrypt(body.api_key)
        config["api_secret"] = encrypt(body.api_secret)
    leader = Leader(name=body.name, source=body.source, config=config, equity_usd=body.equity_usd)
    session.add(leader)
    await session.commit()
    await session.refresh(leader)
    return {"id": leader.id}


# ---- subscriptions ----
@router.get("/subscriptions")
async def list_subscriptions(
    user: User = Depends(current_user), session: AsyncSession = Depends(get_session)
):
    subs = (await session.execute(select(Subscription))).scalars().all()
    return [
        {
            "id": s.id,
            "leader_id": s.leader_id,
            "executor": s.executor.value,
            "mode": s.mode.value,
            "delivery": s.delivery,
            "sizing": s.sizing,
            "equity_usd": s.equity_usd,
            "peak_equity": s.peak_equity,
            "allocation_pct": s.allocation_pct,
            "daily_loss_limit_pct": s.daily_loss_limit_pct,
            "max_drawdown_pct": s.max_drawdown_pct,
            "profit_share_pct": s.profit_share_pct,
            "webhook_url": s.webhook_url,
        }
        for s in subs
    ]


@router.post("/subscriptions")
async def create_subscription(
    body: SubscriptionIn,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    leader = await session.get(Leader, body.leader_id)
    if not leader:
        raise HTTPException(404, "leader not found")
    if body.allocation_pct > 100:
        raise HTTPException(422, "allocation_pct must be 0..100")
    if body.allocation_pct > 0:
        others = (await session.execute(
            select(func.coalesce(func.sum(Subscription.allocation_pct), 0.0))
            .where(Subscription.user_id == user.id)
        )).scalar_one()
        if others + body.allocation_pct > 100.0:
            raise HTTPException(
                422,
                f"allocation sum would be {others + body.allocation_pct:.1f}% (> 100%)",
            )
    sub = Subscription(user_id=user.id, **body.model_dump())
    session.add(sub)
    await session.commit()
    await session.refresh(sub)
    return {"id": sub.id, "allocation_pct": sub.allocation_pct}


@router.patch("/subscriptions/{sub_id}")
async def patch_subscription(
    sub_id: int, body: SubscriptionPatch,
    user: User = Depends(current_user), session: AsyncSession = Depends(get_session),
):
    sub = await session.get(Subscription, sub_id)
    if not sub:
        raise HTTPException(404, "subscription not found")
    updates = body.model_dump(exclude_none=True)
    if "allocation_pct" in updates:
        new_alloc = float(updates["allocation_pct"])
        if new_alloc < 0 or new_alloc > 100:
            raise HTTPException(422, "allocation_pct must be 0..100")
        others = (await session.execute(
            select(func.coalesce(func.sum(Subscription.allocation_pct), 0.0))
            .where(Subscription.user_id == sub.user_id, Subscription.id != sub_id)
        )).scalar_one()
        if others + new_alloc > 100.0:
            raise HTTPException(
                422,
                f"allocation sum would be {others + new_alloc:.1f}% (> 100%)",
            )
    for field, value in updates.items():
        setattr(sub, field, value)
    await session.commit()
    return {"id": sub.id, **updates}


# ---- billing ----
@router.get("/billing")
async def billing_summary(
    subscription_id: int,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    from sqlalchemy import func

    from app.db.models import LedgerEntry, LedgerKind

    sub = await session.get(Subscription, subscription_id)
    if not sub:
        raise HTTPException(404, "subscription not found")
    kind = LedgerEntry.kind
    totals = dict((await session.execute(
        select(kind, func.sum(LedgerEntry.amount_usd))
        .where(LedgerEntry.subscription_id == subscription_id)
        .group_by(kind)
    )).all())
    entries = (await session.execute(
        select(LedgerEntry).where(LedgerEntry.subscription_id == subscription_id)
        .order_by(LedgerEntry.id.desc()).limit(50)
    )).scalars().all()
    return {
        "subscription_id": subscription_id,
        "accrued_platform": totals.get(LedgerKind.PLATFORM_FEE, 0.0),
        "accrued_leader": totals.get(LedgerKind.LEADER_COMMISSION, 0.0),
        "entries": [
            {"kind": e.kind.value, "amount_usd": e.amount_usd, "profit_usd": e.profit_usd,
             "equity_usd": e.equity_usd, "note": e.note, "created_at": e.created_at}
            for e in entries
        ],
    }


@router.get("/leader_earnings")
async def leader_earnings(
    leader_id: int,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    from sqlalchemy import func

    from app.db.models import LedgerEntry, LedgerKind

    total = (await session.execute(
        select(func.sum(LedgerEntry.amount_usd)).where(
            LedgerEntry.leader_id == leader_id,
            LedgerEntry.kind == LedgerKind.LEADER_COMMISSION,
        )
    )).scalar_one_or_none() or 0.0
    return {"leader_id": leader_id, "accrued_leader": total}


# ---- portfolios ----
@router.get("/leader_portfolio")
async def leader_portfolio(
    leader_id: int,
    ccy: str = "USD",
    session: AsyncSession = Depends(get_session),
):
    """Leader's virtual multi-market portfolio (public: built from signals)."""
    from copyflow_core.instruments import static_spec

    from app.engine.leader_portfolio import leader_positions

    md = _md()
    rows = await leader_positions(session, leader_id)
    items = []
    totals_usd = 0.0
    for pos in rows:
        spec = static_spec(pos.venue, pos.symbol)
        market = pos.market or (spec.market if spec else "crypto")
        currency = pos.currency or (spec.currency if spec else "USDT")
        qty_lots = pos.qty / (spec.lot_size if spec and spec.lot_size else 1.0)
        value_native = pos.qty * pos.entry_price
        usd = await md.convert(value_native, currency, "USD") or 0.0
        totals_usd += usd
        items.append({
            "venue": pos.venue, "symbol": pos.symbol, "market": market,
            "side": pos.side, "qty": pos.qty, "qty_lots": round(qty_lots, 4),
            "entry_price": pos.entry_price, "currency": currency,
            "value_native": round(value_native, 2), "value_usd": round(usd, 2),
        })
    display_total = await md.convert(totals_usd, "USD", ccy.upper())
    return {
        "leader_id": leader_id,
        "display_currency": ccy.upper(),
        "total_usd": round(totals_usd, 2),
        "total_display": round(display_total or totals_usd, 2),
        "positions": items,
    }


@router.get("/portfolio")
async def follower_portfolio(
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    """Follower portfolio: allocations across strategies + aggregated positions."""
    from app.engine.copier import default_executor_factory

    md = _md()
    subs = (await session.execute(
        select(Subscription).where(Subscription.user_id == user.id)
    )).scalars().all()

    allocations = []
    positions_all: list[dict] = []
    total_equity_usd = 0.0
    for sub in subs:
        leader = await session.get(Leader, sub.leader_id)
        try:
            adapter = await default_executor_factory.get(sub)
            positions = await adapter.positions()
            state = await adapter.account_state()
        except Exception:
            positions, state = [], {"equity_usd": sub.equity_usd}
        total_equity_usd += float(state.equity_usd)
        allocations.append({
            "subscription_id": sub.id,
            "leader_id": sub.leader_id,
            "leader_name": leader.name if leader else f"#{sub.leader_id}",
            "allocation_pct": sub.allocation_pct,
            "mode": sub.mode.value,
            "equity_usd": float(state.equity_usd),
        })
        for p in positions:
            market, currency = await md.market_currency(p.symbol)
            value_native = p.qty * (p.entry_price or 0)
            usd = await md.convert(value_native, currency, "USD") or 0.0
            positions_all.append({
                "subscription_id": sub.id, "venue": sub.executor.value,
                "symbol": p.symbol, "market": market, "side": p.side,
                "qty": p.qty, "entry_price": p.entry_price,
                "currency": currency, "value_native": round(value_native, 2),
                "value_usd": round(usd, 2),
            })

    allocated = sum(a["allocation_pct"] for a in allocations)
    display = user.portfolio_currency or "USD"
    by_market: dict[str, float] = {}
    for p in positions_all:
        by_market[p["market"]] = by_market.get(p["market"], 0.0) + p["value_usd"]
    total_display = await md.convert(total_equity_usd, "USD", display)

    return {
        "display_currency": display,
        "total_capital_usd": user.total_capital_usd,
        "allocated_pct": round(allocated, 2),
        "unallocated_pct": round(100.0 - allocated, 2),
        "allocations": allocations,
        "positions": positions_all,
        "equity_usd": round(total_equity_usd, 2),
        "equity_display": round(total_display or total_equity_usd, 2),
        "by_market_usd": {k: round(v, 2) for k, v in sorted(by_market.items())},
    }


@router.get("/price")
async def price(venue: str, symbol: str):

    return {"venue": venue, "symbol": symbol, "price": await _md().price(venue, symbol)}


@router.get("/fx")
async def fx(from_ccy: str, to_ccy: str):

    return {"from": from_ccy, "to": to_ccy, "rate": await _md().fx_rate(from_ccy, to_ccy)}


# ---- account / positions ----
@router.get("/positions")
async def positions(
    subscription_id: int,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):

    sub = await session.get(Subscription, subscription_id)
    if not sub:
        raise HTTPException(404, "subscription not found")
    from app.engine.copier import default_executor_factory

    adapter = await default_executor_factory.get(sub)
    state = await adapter.account_state()
    return {
        "equity_usd": state.equity_usd,
        "mode": sub.mode.value,
        "positions": [
            {
                "symbol": p.symbol,
                "side": p.side,
                "qty": p.qty,
                "entry_price": p.entry_price,
            }
            for p in state.positions
        ],
    }


@router.get("/fills")
async def fills(
    subscription_id: int | None = None,
    limit: int = 50,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    q = select(Fill).order_by(Fill.id.desc()).limit(min(limit, 500))
    if subscription_id:
        q = q.where(Fill.subscription_id == subscription_id)
    rows = (await session.execute(q)).scalars().all()
    return [
        {
            "id": f.id,
            "subscription_id": f.subscription_id,
            "symbol": f.symbol,
            "side": f.side,
            "qty": f.qty,
            "price": f.price,
            "fee_usd": f.fee_usd,
        }
        for f in rows
    ]
