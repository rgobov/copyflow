from __future__ import annotations

import pytest
from copyflow_core.executors.base import Position
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.database import make_engine
from app.db.models import Base, Leader, ReconEvent, Subscription, User
from app.engine.reconciler import Reconciler, expected_positions
from app.engine.risk import evaluate_risk, reset_notification_flags


async def make_db(tmp_path):
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path}/r.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    SF = async_sessionmaker(engine, expire_on_commit=False)
    return engine, SF


async def make_sub(SF, daily_loss=10.0, max_dd=25.0, live=False):
    from app.db.models import ExecutionMode

    async with SF() as s:
        u = User(api_key="k")
        s.add(u)
        ld = Leader(name="l", source="strategy")
        s.add(ld)
        await s.flush()
        sub = Subscription(
            user_id=u.id, leader_id=ld.id, daily_loss_limit_pct=daily_loss,
            max_drawdown_pct=max_dd,
            mode=ExecutionMode.LIVE if live else ExecutionMode.PAPER,
        )
        s.add(sub)
        await s.commit()
        return sub.id


async def test_daily_pnl_detection_no_mutation(tmp_path):
    engine, SF = await make_db(tmp_path)
    sub_id = await make_sub(SF, daily_loss=10.0)

    async with SF() as s:
        sub = await s.get(Subscription, sub_id)
        # first check captures day start
        _, r0 = _, _ = await evaluate_risk(s, sub, 1000.0)
        assert not r0.daily_breach
        await s.commit()

    async with SF() as s:
        sub = await s.get(Subscription, sub_id)
        r1 = (await evaluate_risk(s, sub, 880.0))[1]  # -12%
        assert r1.daily_breach
        assert r1.day_pnl_pct == pytest.approx(-12.0)
        assert not r1.drawdown_breach
        # no state mutation: sub is not paused/deleted — client decides
        assert not hasattr(sub, "paused")
    await engine.dispose()


async def test_drawdown_detection_from_peak(tmp_path):
    engine, SF = await make_db(tmp_path)
    sub_id = await make_sub(SF, max_dd=25.0)

    async with SF() as s:
        sub = await s.get(Subscription, sub_id)
        _, _ = await evaluate_risk(s, sub, 1000.0)  # sets peak
        await s.commit()

    async with SF() as s:
        sub = await s.get(Subscription, sub_id)
        r2 = (await evaluate_risk(s, sub, 700.0))[1]  # -30% from peak
        assert r2.drawdown_breach
        assert r2.drawdown_pct == pytest.approx(30.0)
    await engine.dispose()


async def test_notification_edge_flags(tmp_path):
    engine, SF = await make_db(tmp_path)
    sub_id = await make_sub(SF, daily_loss=10.0, max_dd=25.0)

    async with SF() as s:
        sub = await s.get(Subscription, sub_id)
        _, _ = await evaluate_risk(s, sub, 1000.0)
        await s.commit()

    # breach: flags set once
    async with SF() as s:
        sub = await s.get(Subscription, sub_id)
        daily, r = await evaluate_risk(s, sub, 850.0)
        assert r.breached
        daily.daily_loss_notified = True
        await s.commit()

    # still breaching -> no flag reset (no repeated push)
    async with SF() as s:
        sub = await s.get(Subscription, sub_id)
        daily, r = await evaluate_risk(s, sub, 860.0)
        assert r.daily_breach
        await reset_notification_flags(daily, r)
        assert daily.daily_loss_notified
        await s.commit()

    # recovered -> flag reset, next breach will push again
    async with SF() as s:
        sub = await s.get(Subscription, sub_id)
        daily, r = await evaluate_risk(s, sub, 990.0)
        assert not r.daily_breach
        await reset_notification_flags(daily, r)
        assert not daily.daily_loss_notified
    await engine.dispose()


# ---- reconciler ----

async def test_expected_positions_from_fills(tmp_path):
    engine, SF = await make_db(tmp_path)
    sub_id = await make_sub(SF)

    from app.db.models import Fill, Order
    from app.db.models import OrderStatus as OS

    async with SF() as s:
        o = Order(client_order_id="c1", signal_id="s1", subscription_id=sub_id,
                  venue="x", symbol="BTC", side="buy", status=OS.FILLED)
        s.add(o)
        await s.flush()
        s.add(Fill(order_id=o.id, subscription_id=sub_id, symbol="BTC",
                   side="buy", qty=0.5, price=100.0))
        s.add(Fill(order_id=o.id, subscription_id=sub_id, symbol="BTC",
                   side="sell", qty=0.2, price=110.0))
        await s.commit()

    async with SF() as s:
        pos = await expected_positions(s, sub_id)
        assert set(pos) == {"BTC"}
        assert pos["BTC"].side == "long"
        assert pos["BTC"].qty == pytest.approx(0.3)
        # entry basis: 0.5*100 + 0.2*110 = 72 / 0.3 = 240
        assert pos["BTC"].entry_price == pytest.approx(240.0)
    await engine.dispose()


async def test_reconciler_detects_mismatch(tmp_path):
    engine, SF = await make_db(tmp_path)
    sub_id = await make_sub(SF, live=True)

    # ledger says long 1.0
    async with SF() as s:
        from app.db.models import Fill, Order
        from app.db.models import OrderStatus as OS

        o = Order(client_order_id="c1", signal_id="s1", subscription_id=sub_id,
                  venue="hyperliquid", symbol="BTC", side="buy", status=OS.FILLED)
        s.add(o)
        await s.flush()
        s.add(Fill(order_id=o.id, subscription_id=sub_id, symbol="BTC",
                   side="buy", qty=1.0, price=100.0))
        await s.commit()

    class StubFactory:
        class _A:
            venue = "hyperliquid"

            async def positions(self):
                return [Position(symbol="BTC", side="long", qty=0.4, entry_price=100.0)]

        async def get(self, sub):
            return self._A()

    sent: list[dict] = []

    class StubDelivery:
        async def send_risk(self, session, sub, payload):
            sent.append(payload)

    rec = Reconciler(SF, StubFactory(), delivery=StubDelivery(), interval=9999)
    events = await rec.reconcile_once()
    assert len(events) == 1
    assert "qty ledger 1.0" in events[0]["problem"]
    assert sent and "recon mismatch" in sent[0]["reason"]

    async with SF() as s:
        rows = (await s.execute(select(ReconEvent))).scalars().all()
        assert len(rows) == 1
    await engine.dispose()


async def test_reconciler_silent_when_matched(tmp_path):
    engine, SF = await make_db(tmp_path)
    sub_id = await make_sub(SF, live=True)

    async with SF() as s:
        from app.db.models import Fill, Order
        from app.db.models import OrderStatus as OS

        o = Order(client_order_id="c1", signal_id="s1", subscription_id=sub_id,
                  venue="hyperliquid", symbol="ETH", side="sell", status=OS.FILLED)
        s.add(o)
        await s.flush()
        s.add(Fill(order_id=o.id, subscription_id=sub_id, symbol="ETH",
                   side="sell", qty=2.0, price=3000.0))
        await s.commit()

    class StubFactory:
        class _A:
            venue = "hyperliquid"

            async def positions(self):
                return [Position(symbol="ETH", side="short", qty=2.0, entry_price=3000.0)]

        async def get(self, sub):
            return self._A()

    rec = Reconciler(SF, StubFactory(), interval=9999)
    assert await rec.reconcile_once() == []
    await engine.dispose()
