from __future__ import annotations

import pytest
from copyflow_core.executors.paper import PaperConfig, PaperExecutor
from copyflow_core.models import Action, Instrument, Side, SignalEvent

from app.core.bus import EventBus
from app.db.database import make_engine
from app.db.models import Base, ExecutionMode, ExecutorKind, Leader, Subscription, User
from app.engine.copier import CopyEngine, ExecutorFactory


class FakeExecutor(PaperExecutor):
    """PaperExecutor with fixed price -> deterministic fills."""

    def __init__(self):
        async def p(symbol):
            return 100_000.0

        super().__init__(PaperConfig(starting_equity_usd=1_000.0), price_provider=p)


class RecordingFactory(ExecutorFactory):
    def __init__(self, executor):
        self._executor = executor
        self._cache = {}

    async def get(self, sub):
        self._cache.setdefault(sub.id, self._executor)
        return self._cache[sub.id]


async def test_end_to_end_open_and_close(tmp_path):
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path}/t.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker

    SF = async_sessionmaker(engine, expire_on_commit=False)

    bus = EventBus()
    executor = FakeExecutor()
    engine_srv = CopyEngine(bus, SF, RecordingFactory(executor))

    async with SF() as s:
        user = User(api_key="k1")
        s.add(user)
        leader = Leader(name="strat", source="strategy", equity_usd=10_000)
        s.add(leader)
        await s.flush()
        sub = Subscription(
            user_id=user.id, leader_id=leader.id,
            executor=ExecutorKind.PAPER, mode=ExecutionMode.PAPER,
            equity_usd=1_000,
        )
        s.add(sub)
        await s.commit()
        sub_id, leader_id = sub.id, leader.id

    engine_srv.start()
    event = SignalEvent(
        leader_id=leader_id,
        instrument=Instrument(venue="paper", symbol="BTC"),
        action=Action.OPEN, side=Side.LONG, size_frac=0.5, ref_price=100_000,
    )
    await engine_srv.handle_signal(event)

    async with SF() as s:
        subs = (await s.execute(select(Subscription).where(Subscription.id == sub_id))).scalar_one()
        adapter = await engine_srv._factory.get(subs)
        positions = await adapter.positions()
        assert len(positions) == 1
        # 10 bps default slippage -> fill at 100010
        assert positions[0].qty == pytest.approx(0.005, rel=0.01)

        # close
        ev_close = SignalEvent(
            leader_id=leader_id,
            instrument=Instrument(venue="paper", symbol="BTC"),
            action=Action.CLOSE, side=Side.LONG, size_frac=1.0, ref_price=100_000,
        )
        await engine_srv.handle_signal(ev_close)
        assert await adapter.positions() == []

        # signal persisted once per unique id
        from app.db.models import Order, Signal
        orders = (await s.execute(select(Order))).scalars().all()
        assert len(orders) == 2
        assert all(o.status.value == "filled" for o in orders)
        sigs = (await s.execute(select(Signal))).scalars().all()
        assert len(sigs) == 2

    await engine_srv.stop()
    await bus.close_all()
    await engine.dispose()


async def test_idempotent_signal(tmp_path):
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path}/t2.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker

    SF = async_sessionmaker(engine, expire_on_commit=False)
    bus = EventBus()
    executor = FakeExecutor()
    engine_srv = CopyEngine(bus, SF, RecordingFactory(executor))

    async with SF() as s:
        user = User(api_key="k1")
        s.add(user)
        leader = Leader(name="strat", source="strategy", equity_usd=10_000)
        s.add(leader)
        await s.flush()
        s.add(Subscription(user_id=user.id, leader_id=leader.id, equity_usd=1_000))
        await s.commit()

    event = SignalEvent(
        leader_id=1, instrument=Instrument(venue="paper", symbol="BTC"),
        action=Action.OPEN, side=Side.LONG, size_frac=0.5, ref_price=100_000,
    )
    await engine_srv.handle_signal(event)
    await engine_srv.handle_signal(event)  # duplicate

    async with SF() as s:
        from app.db.models import Order
        orders = (await s.execute(select(Order))).scalars().all()
        assert len(orders) == 1
        assert orders[0].status.value == "filled"

    await engine.dispose()
