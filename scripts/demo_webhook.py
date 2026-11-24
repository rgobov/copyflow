"""Live demo: server -> signed webhook -> client app.

Starts the copyflow API (port 8000) and a fake client app (port 9001) that
receives the signed webhook, verifies the HMAC and prints what it got.
Run: .venv/bin/python scripts/demo_webhook.py
"""
import asyncio
import secrets
import threading
import time

import httpx
import uvicorn
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.database import make_engine
from app.db.models import Base, User
from app.delivery.channels import sign

API = "http://127.0.0.1:8000/api/v1"
CLIENT_PORT = 9001
received: list = []


# ---- fake client app ----
def run_client() -> None:
    from fastapi import FastAPI, Request

    app = FastAPI()

    @app.post("/hook")
    async def hook(request: Request):
        body = await request.body()
        sig = request.headers.get("X-Copyflow-Signature", "")
        ts = request.headers.get("X-Copyflow-Timestamp", "")
        ok = sig == sign(CLIENT_SECRET, body, ts)
        import json

        received.append({"signature_ok": ok, "payload": json.loads(body)})
        return {"ok": True}

    uvicorn.run(app, host="127.0.0.1", port=CLIENT_PORT, log_level="error")


async def main() -> None:
    engine = make_engine("sqlite+aiosqlite:///./copyflow.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    SF = async_sessionmaker(engine, expire_on_commit=False)
    async with SF() as s:
        s.add(User(api_key=API_KEY, display_name="demo"))
        await s.commit()
    await engine.dispose()

    t = threading.Thread(target=run_client, daemon=True)
    t.start()
    await asyncio.sleep(1.5)

    async with httpx.AsyncClient(base_url=API, timeout=15) as c:
        h = {"X-Api-Key": API_KEY}
        leader = (await c.post("/leaders", headers=h, json={
            "name": "VBO ORB", "source": "strategy", "equity_usd": 10_000,
        })).json()
        sub = (await c.post("/subscriptions", headers=h, json={
            "leader_id": leader["id"],
            "delivery": "webhook",
            "webhook_url": f"http://127.0.0.1:{CLIENT_PORT}/hook",
            "webhook_secret": CLIENT_SECRET,
            "daily_loss_limit_pct": 5.0,
            "max_drawdown_pct": 25.0,
        })).json()
        print(f"[1] leader #{leader['id']} + subscription #{sub['id']} (delivery=webhook)")

        sig = (await c.post("/signals", headers=h, json={
            "leader_id": leader["id"], "symbol": "BTC", "action": "open",
            "side": "long", "size_frac": 0.3, "ref_price": 95_000,
            "take_profit": 99_000, "stop_loss": 93_000,
        })).json()
        print(f"[2] strategy posted signal {sig['signal_id'][:12]}...")

        for _ in range(30):
            if received:
                break
            await asyncio.sleep(0.3)

        r = received[0]
        s = r["payload"]["signal"]
        print(f"[3] client received webhook, signature_ok={r['signature_ok']}")
        print(f"    {s['action'].upper()} {s['instrument']['symbol']} {s['side']} "
              f"size_frac={s['size_frac']} tp={s['take_profit']} sl={s['stop_loss']}")
        print(f"    risk limits: {r['payload']['risk']}")

        # risk push: simulate drawdown breach via PATCH + monitor pass is server-side;
        # here we just confirm no orders were placed server-side
        fills = (await c.get("/fills", headers=h, params={"limit": 5})).json()
        print(f"[4] server placed orders: {len(fills)} (client model: server didn't trade)")


if __name__ == "__main__":
    API_KEY = secrets.token_hex(16)
    CLIENT_SECRET = secrets.token_hex(16)
    asyncio.run(main())
    time.sleep(0.2)
