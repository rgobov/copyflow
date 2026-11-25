from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{Path(__file__).parent / 'tmp_api.db'}"

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker  # noqa: E402

from app.db.models import Base, User  # noqa: E402


@pytest.fixture()
def client(tmp_path):
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{tmp_path}/api.db"
    from app.db import database as db
    from app.main import app

    db.engine = db.make_engine(os.environ["DATABASE_URL"])
    db.SessionFactory = db.make_session_factory(engine=db.engine)

    async def seed():
        engine = db.make_engine(os.environ["DATABASE_URL"])
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        SF = async_sessionmaker(engine, expire_on_commit=False)
        async with SF() as s:
            s.add(User(api_key="webkey", display_name="web"))
            await s.commit()
        await engine.dispose()

    import asyncio

    asyncio.run(seed())
    with TestClient(app) as c:
        yield c


def test_api_surface(client):
    c = client
    h = {"X-Api-Key": "webkey"}

    # auth required without key
    assert c.get("/api/v1/me").status_code == 401

    me = c.get("/api/v1/me", headers=h).json()
    assert me["display_name"] == "web"

    r = c.post("/api/v1/leaders", headers=h, json={"name": "VBO ORB", "equity_usd": 10_000})
    assert r.status_code == 200

    for sym, side in (("BTC", "long"), ("ETH", "short")):
        r = c.post("/api/v1/signals", headers=h, json={
            "leader_id": 1, "symbol": sym, "action": "open", "side": side,
            "size_frac": 0.4, "ref_price": 1000.0,
        })
        assert r.status_code == 200

    # engine persists signals asynchronously
    import time

    feed = []
    for _ in range(20):
        feed = c.get("/api/v1/signals", headers=h).json()
        if len(feed) == 2:
            break
        time.sleep(0.2)
    assert len(feed) == 2
    assert feed[0]["leader_name"] == "VBO ORB"
    assert {f["symbol"] for f in feed} == {"BTC", "ETH"}

    leaders = c.get("/api/v1/leaders", headers=h).json()
    assert leaders[0]["signal_count"] == 2
    assert leaders[0]["last_signal_at"] is not None
    assert leaders[0]["followers"] == 0


def test_billing_summary(client):
    c = client
    h = {"X-Api-Key": "webkey"}
    c.post("/api/v1/leaders", headers=h, json={"name": "L", "equity_usd": 10_000})
    c.post("/api/v1/subscriptions", headers=h, json={"leader_id": 1, "executor": "paper", "mode": "paper"})

    # write ledger entries directly (engine accrual covered in test_billing)
    from app.db.models import LedgerEntry, LedgerKind

    async def seed_entries():
        from app.db import database as db

        SF = db.make_session_factory(db.engine)
        async with SF() as s:
            s.add(LedgerEntry(subscription_id=1, leader_id=1, kind=LedgerKind.PLATFORM_FEE,
                              amount_usd=4.5, profit_usd=100, equity_usd=1100, hwm_before=1000))
            s.add(LedgerEntry(subscription_id=1, leader_id=1, kind=LedgerKind.LEADER_COMMISSION,
                              amount_usd=10.5, profit_usd=100, equity_usd=1100, hwm_before=1000))
            await s.commit()

    import asyncio

    asyncio.run(seed_entries())

    r = c.get("/api/v1/billing?subscription_id=1", headers=h).json()
    assert r["accrued_platform"] == pytest.approx(4.5)
    assert r["accrued_leader"] == pytest.approx(10.5)
    assert len(r["entries"]) == 2

    r = c.get("/api/v1/leader_earnings?leader_id=1", headers=h).json()
    assert r["accrued_leader"] == pytest.approx(10.5)

    assert c.get("/api/v1/billing?subscription_id=99", headers=h).status_code == 404
