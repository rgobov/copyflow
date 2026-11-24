from __future__ import annotations

from copyflow_core.models import Action, Instrument, Side, SignalEvent
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_session
from app.db.models import ExecutionMode, ExecutorKind, Fill, Leader, Subscription, User

router = APIRouter(prefix="/api/v1")


# ---- auth ----
async def current_user(
    x_api_key: str = Header(...), session: AsyncSession = Depends(get_session)
) -> User:
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


class SubscriptionIn(BaseModel):
    leader_id: int
    executor: ExecutorKind = ExecutorKind.PAPER
    mode: ExecutionMode = ExecutionMode.PAPER
    delivery: str = "auto"  # auto | webhook | telegram
    webhook_url: str | None = None
    webhook_secret: str | None = None
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
    leaders = (await session.execute(select(Leader))).scalars().all()
    return [
        {
            "id": ld.id,
            "name": ld.name,
            "source": ld.source,
            "equity_usd": ld.equity_usd,
            "is_active": ld.is_active,
        }
        for ld in leaders
    ]


@router.post("/leaders")
async def create_leader(
    body: LeaderIn, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)
):
    leader = Leader(name=body.name, source=body.source, config=body.config, equity_usd=body.equity_usd)
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
            "daily_loss_limit_pct": s.daily_loss_limit_pct,
            "max_drawdown_pct": s.max_drawdown_pct,
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
    sub = Subscription(user_id=user.id, **body.model_dump())
    session.add(sub)
    await session.commit()
    await session.refresh(sub)
    return {"id": sub.id}


@router.patch("/subscriptions/{sub_id}")
async def patch_subscription(
    sub_id: int, body: SubscriptionPatch,
    user: User = Depends(current_user), session: AsyncSession = Depends(get_session),
):
    sub = await session.get(Subscription, sub_id)
    if not sub:
        raise HTTPException(404, "subscription not found")
    updates = body.model_dump(exclude_none=True)
    for field, value in updates.items():
        setattr(sub, field, value)
    await session.commit()
    return {"id": sub.id, **updates}


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
