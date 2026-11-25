from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import settings
from app.db.database import make_engine
from app.db.models import Base, ExecutionMode, Leader, LedgerEntry, LedgerKind, Subscription, User
from app.engine.billing import accrue_fees


@pytest.fixture()
def db(tmp_path):
    async def _make():
        engine = make_engine(f"sqlite+aiosqlite:///{tmp_path}/b.db")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        return engine, async_sessionmaker(engine, expire_on_commit=False)
    return _make


async def make_sub(SF, mode=ExecutionMode.LIVE, ps=None, ls=None):
    import secrets

    async with SF() as s:
        u = User(api_key=secrets.token_hex(8))
        s.add(u)
        ld = Leader(name="l", source="strategy")
        s.add(ld)
        await s.flush()
        sub = Subscription(
            user_id=u.id, leader_id=ld.id, mode=mode,
            profit_share_pct=ps, leader_share_pct=ls,
        )
        s.add(sub)
        await s.commit()
        return sub.id


async def test_accrue_on_new_high_water_mark(db):
    engine, SF = await db()
    sub_id = await make_sub(SF, ps=15.0, ls=70.0)

    async with SF() as s:
        sub = await s.get(Subscription, sub_id)
        # equity 1000 sets peak; no charge
        e1 = await accrue_fees(s, sub, 1000.0)
        assert e1 is None
        await s.commit()

    async with SF() as s:
        sub = await s.get(Subscription, sub_id)
        # +100 profit -> 15% fee: leader 10.5, platform 4.5
        e2 = await accrue_fees(s, sub, 1100.0)
        assert e2 is not None
        assert sub.peak_equity == pytest.approx(1100.0)
        await s.commit()

    async with SF() as s:
        sub = await s.get(Subscription, sub_id)
        # dip below hwm -> no charge
        assert await accrue_fees(s, sub, 1050.0) is None
        # new high above hwm 1100 -> profit counted from 1100
        e3 = await accrue_fees(s, sub, 1200.0)
        assert e3 is not None
        assert e3.profit_usd == pytest.approx(100.0)
        await s.commit()

    async with SF() as s:
        entries = (await s.execute(select(LedgerEntry).order_by(LedgerEntry.id))).scalars().all()
        platform = [e for e in entries if e.kind == LedgerKind.PLATFORM_FEE]
        leader = [e for e in entries if e.kind == LedgerKind.LEADER_COMMISSION]
        assert len(platform) == 2 and len(leader) == 2
        assert sum(e.amount_usd for e in platform) == pytest.approx(9.0)   # 4.5 * 2
        assert sum(e.amount_usd for e in leader) == pytest.approx(21.0)    # 10.5 * 2
    await engine.dispose()


async def test_no_charge_for_paper_or_zero_pct(db):
    engine, SF = await db()
    paper_id = await make_sub(SF, mode=ExecutionMode.PAPER, ps=15.0)
    free_id = await make_sub(SF, mode=ExecutionMode.LIVE, ps=0.0)

    async with SF() as s:
        sub = await s.get(Subscription, paper_id)
        assert await accrue_fees(s, sub, 2000.0) is None
        sub = await s.get(Subscription, free_id)
        assert await accrue_fees(s, sub, 2000.0) is None
    await engine.dispose()


async def test_global_defaults_apply(db):
    old = (settings.billing_profit_share_pct, settings.billing_leader_share_pct)
    settings.billing_profit_share_pct = 20.0
    settings.billing_leader_share_pct = 50.0
    try:
        engine, SF = await db()
        sub_id = await make_sub(SF, ps=None, ls=None)  # inherit globals

        async with SF() as s:
            sub = await s.get(Subscription, sub_id)
            await accrue_fees(s, sub, 1000.0)
            await s.commit()

        async with SF() as s:
            sub = await s.get(Subscription, sub_id)
            e = await accrue_fees(s, sub, 1100.0)
            assert e is not None
            await s.commit()

        async with SF() as s:
            entries = (await s.execute(select(LedgerEntry))).scalars().all()
            platform = sum(e.amount_usd for e in entries if e.kind == LedgerKind.PLATFORM_FEE)
            leader = sum(e.amount_usd for e in entries if e.kind == LedgerKind.LEADER_COMMISSION)
            # total 20 (20% of 100), split 50/50
            assert platform == pytest.approx(10.0)
            assert leader == pytest.approx(10.0)
        await engine.dispose()
    finally:
        settings.billing_profit_share_pct, settings.billing_leader_share_pct = old
