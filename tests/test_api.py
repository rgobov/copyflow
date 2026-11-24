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
