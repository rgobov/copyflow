"""End-to-end smoke: create leader + paper subscription, push a signal, check positions.

Run: .venv/bin/python scripts/smoke.py
"""
import asyncio
import secrets

import httpx
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.database import make_engine
from app.db.models import Base, User


async def main() -> None:
    engine = make_engine("sqlite+aiosqlite:///./copyflow.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    SF = async_sessionmaker(engine, expire_on_commit=False)

    async with SF() as s:
        api_key = secrets.token_hex(16)
        s.add(User(api_key=api_key, display_name="smoke"))
        await s.commit()

    async with httpx.AsyncClient(base_url="http://127.0.0.1:8000") as c:
        h = {"X-Api-Key": api_key}
        leader = (await c.post("/api/v1/leaders", headers=h, json={
            "name": "VBO ORB (paper)", "source": "strategy", "equity_usd": 10_000,
        })).json()
        sub = (await c.post("/api/v1/subscriptions", headers=h, json={
            "leader_id": leader["id"], "executor": "paper", "mode": "paper",
            "equity_usd": 1_000,
        })).json()
        print("leader:", leader, "\nsubscription:", sub)

        r = await c.post("/api/v1/signals", headers=h, json={
            "leader_id": leader["id"], "symbol": "BTC", "action": "open",
            "side": "long", "size_frac": 0.3, "ref_price": 95_000,
        })
        print("signal:", r.json())

        async def wait_positions(sub_id: int) -> dict:
            for _ in range(20):
                p = (await c.get("/api/v1/positions", headers=h,
                                 params={"subscription_id": sub_id})).json()
                if p["positions"]:
                    return p
                await asyncio.sleep(0.3)
            return p

        pos = await wait_positions(sub["id"])
        print("positions:", pos)

        await c.post("/api/v1/signals", headers=h, json={
            "leader_id": leader["id"], "symbol": "BTC", "action": "close",
            "side": "long", "size_frac": 1.0, "ref_price": 95_000,
        })
        await asyncio.sleep(1.5)
        pos = (await c.get("/api/v1/positions", headers=h,
                           params={"subscription_id": sub["id"]})).json()
        fills = (await c.get("/api/v1/fills", headers=h, params={"limit": 5})).json()
        print("positions after close:", pos, "\nfills:", fills)

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
