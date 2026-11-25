from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field

import pytest
from copyflow_core.models import Action, Side
from cryptography.fernet import Fernet

from app.config import settings
from app.sources.base import LeaderPositionPoller
from app.sources.cex_leader_poller import CexLeaderPoller, decrypt_leader_credentials


class RecordingBus:
    def __init__(self):
        self.events = []

    async def publish(self, event):
        self.events.append(event)


class FakePoller(LeaderPositionPoller):
    venue = "test"

    async def _leaders(self):
        return []

    async def _fetch(self, leader):
        return {}, {}, 0.0


def run_diff(poller, equity, prices, prev, current):
    async def go():
        return await poller._diff(1, equity, prices, prev, current)

    return asyncio.run(go())


def test_diff_open_increase_reduce_flip_close():
    poller = FakePoller(RecordingBus(), session_factory=None)

    def snap(szi, side):
        return {"szi": szi, "side": side}

    # open: long 1 BTC @ 100, equity 1000 -> frac 0.1
    ev = run_diff(poller, 1000, {"BTC": 100}, {}, {"BTC": snap(1, "long")})
    assert ev[0].action == Action.OPEN and ev[0].size_frac == pytest.approx(0.1)
    assert ev[0].side == Side.LONG

    # increase to 1.5 -> +0.05
    ev = run_diff(poller, 1000, {"BTC": 100}, {"BTC": snap(1, "long")}, {"BTC": snap(1.5, "long")})
    assert ev[0].action == Action.INCREASE and ev[0].size_frac == pytest.approx(0.05)

    # reduce to 1.0 -> -0.05
    ev = run_diff(poller, 1000, {"BTC": 100}, {"BTC": snap(1.5, "long")}, {"BTC": snap(1, "long")})
    assert ev[0].action == Action.REDUCE and ev[0].size_frac == pytest.approx(0.05)

    # flip to short 2 -> new notional 200 -> frac 0.2
    ev = run_diff(poller, 1000, {"BTC": 100}, {"BTC": snap(1, "long")}, {"BTC": snap(2, "short")})
    assert ev[0].action == Action.FLIP and ev[0].side == Side.SHORT
    assert ev[0].size_frac == pytest.approx(0.2)

    # close
    ev = run_diff(poller, 1000, {"BTC": 100}, {"BTC": snap(2, "short")}, {})
    assert ev[0].action == Action.CLOSE and ev[0].size_frac == 1.0


def test_diff_no_change_no_events():
    poller = FakePoller(RecordingBus(), session_factory=None)
    cur = {"BTC": {"szi": 1, "side": "long"}}
    ev = run_diff(poller, 1000, {"BTC": 100}, cur, cur)
    assert ev == []


# ---- CEX leader poller ----

@dataclass
class StubState:
    equity_usd: float = 5000.0
    positions: list = field(default_factory=list)


@dataclass
class StubPos:
    symbol: str
    side: str
    qty: float
    entry_price: float


class StubAdapter:
    venue = "binance"

    async def account_state(self):
        return StubState(equity_usd=5000.0, positions=[
            StubPos(symbol="BTC", side="long", qty=0.5, entry_price=100.0),
            StubPos(symbol="ETH", side="short", qty=4.0, entry_price=50.0),
        ])

    async def mark_price(self, symbol):
        return {"BTC": 100.0, "ETH": 50.0}[symbol]


async def test_cex_poller_maps_positions():
    old = settings.fernet_key
    settings.fernet_key = Fernet.generate_key().decode()
    try:
        from app.security import encrypt

        bus = RecordingBus()
        poller = CexLeaderPoller(bus, session_factory=None, poll_interval=9999)

        class FakeLeader:
            id = 5
            equity_usd = 10_000.0
            config = {
                "exchange": "binance",
                "api_key": encrypt("k"),
                "api_secret": encrypt("s"),
            }

        def get_adapter(leader_id, k, s):
            return StubAdapter()

        async def leaders():
            return [FakeLeader()]

        poller._adapter = get_adapter
        poller._leaders = leaders

        events = await poller.poll_once()
    finally:
        settings.fernet_key = old

    actions = {(e.instrument.symbol, e.action) for e in events}
    assert ("BTC", Action.OPEN) in actions
    assert ("ETH", Action.OPEN) in actions
    btc = next(e for e in events if e.instrument.symbol == "BTC")
    # notional 0.5*100 = 50 -> frac 50/5000 = 0.01
    assert btc.size_frac == pytest.approx(0.01)


async def test_cex_credentials_encrypted_at_rest(tmp_path):
    old = settings.fernet_key
    settings.fernet_key = Fernet.generate_key().decode()
    try:
        from app.security import encrypt

        class Leader:
            config = {
                "exchange": "binance",
                "api_key": encrypt("plain-key"),
                "api_secret": encrypt("plain-secret"),
            }

        creds = decrypt_leader_credentials(Leader())
        assert creds == {"exchange": "binance", "api_key": "plain-key", "api_secret": "plain-secret"}
        # encrypted form never contains plaintext
        assert "plain-key" not in json.dumps(Leader().config)
    finally:
        settings.fernet_key = old
