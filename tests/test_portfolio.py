from __future__ import annotations

import pytest
from copyflow_core.executors.paper import PaperConfig, PaperExecutor
from copyflow_core.models import Action, Instrument, Side, SignalEvent
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.database import make_engine
from app.db.models import Base, ExecutorKind, Leader, LeaderPosition, Subscription, User
from app.engine.copier import CopyEngine, ExecutorFactory


class FixedPaper(PaperExecutor):
    def __init__(self):
        async def p(symbol):
            return 100.0

        super().__init__(PaperConfig(starting_equity_usd=1000.0), price_provider=p)


class SeqFactory(ExecutorFactory):
    def __init__(self, executor):
        super().__init__()
        self._executor = executor

    async def get(self, sub):
        return self._executor


def ev(action: Action, symbol: str = "BTC", venue: str = "binance",
       side: Side = Side.LONG, frac: float | None = 0.5, ref: float = 100.0) -> SignalEvent:
    return SignalEvent(
        leader_id=1,
        instrument=Instrument(venue=venue, symbol=symbol),
        action=action, side=side, size_frac=frac, ref_price=ref,
    )


async def setup(tmp_path):
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path}/lp.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    SF = async_sessionmaker(engine, expire_on_commit=False)
    async with SF() as s:
        u = User(api_key="k1", total_capital_usd=1000.0)
        s.add(u)
        ld = Leader(name="Multi-market", source="strategy", equity_usd=10_000)
        s.add(ld)
        await s.flush()
        s.add(Subscription(user_id=u.id, leader_id=ld.id,
                           executor=ExecutorKind.PAPER, equity_usd=1000))
        await s.commit()
    from app.core.bus import EventBus
    eng = CopyEngine(EventBus(), SF, SeqFactory(FixedPaper()))
    return engine, SF, eng


async def test_leader_portfolio_multi_market(tmp_path):
    engine, SF, eng = await setup(tmp_path)

    # leader opens BTC (crypto) and SBER (moex) — virtual portfolio
    await eng.handle_signal(ev(Action.OPEN, "BTC", "binance", Side.LONG, 0.5))
    await eng.handle_signal(ev(Action.OPEN, "SBER", "moex", Side.LONG, 0.3))

    async with SF() as s:
        rows = (await s.execute(select(LeaderPosition).order_by(LeaderPosition.symbol))).scalars().all()
        assert len(rows) == 2
        btc = next(r for r in rows if r.symbol == "BTC")
        sber = next(r for r in rows if r.symbol == "SBER")
        # BTC: 50% of 10k = 5000 / 100 = 50 BTC-units; crypto meta
        assert btc.market == "crypto" and btc.currency == "USDT"
        assert btc.qty == pytest.approx(50.0)
        # SBER: 30% of 10k = 3000 / 100 = 30 shares, ru meta
        assert sber.market == "ru" and sber.currency == "RUB"
        assert sber.qty == pytest.approx(30.0)

        # close SBER
        await eng.handle_signal(ev(Action.CLOSE, "SBER", "moex", Side.LONG, 1.0))
        left = (await s.execute(select(LeaderPosition))).scalars().all()
        assert [r.symbol for r in left] == ["BTC"]

    await engine.dispose()


async def test_strict_allocation_sizing(tmp_path):
    engine, SF, eng = await setup(tmp_path)

    # user total 1000, allocation 0 -> skip; 50% -> size 500 notional
    async with SF() as s:
        sub = (await s.execute(select(Subscription))).scalar_one()
        r = await eng.handle_signal(ev(Action.OPEN, "BTC", "binance", Side.LONG, 0.5))
        o = await s.execute(select(__import__("app.db.models", fromlist=["Order"]).Order)
                            .order_by(__import__("app.db.models", fromlist=["Order"]).Order.id.desc()))
        sub = await s.get(Subscription, sub.id)

    from app.db.models import Order

    async with SF() as s:
        orders = (await s.execute(select(Order).order_by(Order.id))).scalars().all()
        # allocation 0 -> skipped
        assert orders[-1].status.value == "skipped"
        assert "no allocation" in orders[-1].skip_reason

        # set allocation 50%
        sub = (await s.execute(select(Subscription))).scalar_one()
        sub.allocation_pct = 50.0
        await s.commit()

    await eng.handle_signal(ev(Action.OPEN, "ETH", "binance", Side.LONG, 0.5))
    async with SF() as s:
        orders = (await s.execute(select(Order).order_by(Order.id))).scalars().all()
        assert orders[-1].status.value == "filled"
        # effective equity = 1000 * 50% = 500; leader frac 0.5 of 10k -> frac 0.5 -> 250 notional
        assert orders[-1].notional_usd == pytest.approx(250.0)
    await engine.dispose()
