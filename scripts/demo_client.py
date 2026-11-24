"""E2E: copyflow server -> client app -> local paper execution.

Starts copyflow server (:8000) and copyflow-client (:9000), creates a webhook
subscription on the server, pushes a strategy signal and shows the client
executing it locally with its own risk limits.
Run: .venv/bin/python scripts/demo_client.py
"""
import asyncio
import json
import os
import secrets
import subprocess
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
API = "http://127.0.0.1:8000/api/v1"
CLIENT = "http://127.0.0.1:9000"
SECRET = secrets.token_hex(16)
API_KEY = "demo-key-please-create"


def start_proc(cmd: list[str], log: str):
    f = open(log, "w")
    return subprocess.Popen(cmd, stdout=f, stderr=subprocess.STDOUT, cwd=ROOT)


async def wait_ready(client: httpx.AsyncClient, url: str, timeout=30):
    for _ in range(int(timeout * 4)):
        try:
            r = await client.get(url)
            if r.status_code < 500:
                return
        except httpx.HTTPError:
            pass
        await asyncio.sleep(0.25)
    raise RuntimeError(f"{url} not ready")


async def main() -> None:
    # server user for API auth (direct DB insert, like smoke)
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.db.database import make_engine
    from app.db.models import Base, User

    engine = make_engine("sqlite+aiosqlite:///./copyflow.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    SF = async_sessionmaker(engine, expire_on_commit=False)
    async with SF() as s:
        if not (await s.execute(select(User).where(User.api_key == API_KEY))).scalar_one_or_none():
            s.add(User(api_key=API_KEY, display_name="demo"))
        await s.commit()
    await engine.dispose()

    (ROOT / "client.json").write_text(json.dumps({
        "subscriptions": {
            "1": {
                "name": "VBO ORB follow",
                "executor": "paper",
                "confirm_mode": "auto",
                "equity_usd": 1000,
                "leverage_cap": 2.0,
                "daily_loss_limit_pct": 5.0,
                "max_drawdown_pct": 20.0,
                "on_breach": "pause_and_close",
            }
        }
    }, indent=2))

    server = start_proc([".venv/bin/uvicorn", "app.main:app", "--port", "8000"], "/tmp/opencode/srv.log")
    client_env = {**os.environ, "CF_WEBHOOK_SECRET": SECRET}
    f_cli = open("/tmp/opencode/cli.log", "w")
    client_proc = subprocess.Popen(
        [".venv/bin/uvicorn", "client.main:app", "--port", "9000"],
        stdout=f_cli, stderr=subprocess.STDOUT, cwd=ROOT, env=client_env,
    )
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            await wait_ready(c, API.rsplit("/api", 1)[0] + "/api/v1/health")
            await wait_ready(c, CLIENT + "/status")

            # 1. server: leader + webhook subscription
            h = {"X-Api-Key": API_KEY}
            await c.post(API + "/leaders", headers=h, json={"name": "VBO ORB", "source": "strategy", "equity_usd": 10_000})
            r = await c.post(API + "/subscriptions", headers=h, json={
                "leader_id": 1, "delivery": "webhook",
                "webhook_url": "http://127.0.0.1:9000/hook",
                "webhook_secret": SECRET,
            })
            print("[server] leader + webhook subscription:", r.json())

            # 2. strategy posts a signal
            r = await c.post(API + "/signals", headers=h, json={
                "leader_id": 1, "symbol": "BTC", "action": "open", "side": "long",
                "size_frac": 0.3, "ref_price": 95_000,
            })
            print("[server] strategy signal:", r.json())
            await asyncio.sleep(1.5)

            # 3. client executed locally with its own limits
            st = (await c.get(CLIENT + "/status")).json()
            acc = st["accounts"][0]
            print(f"[client] executed on {acc['executor']}: {acc['positions']}")
            print(f"[client] equity {acc['equity_usd']}, day pnl {acc['day_pnl_pct']}%")
            print(f"[client] decisions: {st['recent_decisions']}")
    finally:
        server.terminate()
        client_proc.terminate()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
