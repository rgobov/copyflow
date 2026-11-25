from __future__ import annotations

import logging

from copyflow_core.executors.base import ExecutionError
from copyflow_core.executors.binance import BinanceExecutor

from app.security import decrypt
from app.sources.base import LeaderPositionPoller

log = logging.getLogger(__name__)


def decrypt_leader_credentials(leader) -> dict:
    """Leader.config for cex_trader holds encrypted exchange keys:
    {"exchange": "binance", "api_key": <fernet>, "api_secret": <fernet>}."""
    cfg = leader.config or {}
    return {
        "exchange": cfg.get("exchange", "binance"),
        "api_key": decrypt(cfg["api_key"]) if cfg.get("api_key") else "",
        "api_secret": decrypt(cfg["api_secret"]) if cfg.get("api_secret") else "",
    }


class CexLeaderPoller(LeaderPositionPoller):
    """Follows CEX traders who share READ-ONLY API keys.

    Keys are stored encrypted on the Leader; the poller reads their futures
    positions with trade/withdraw permissions disabled and emits diff-based
    signals exactly like on-chain wallet tracking.
    """

    venue = "binance"

    def __init__(self, bus, session_factory, poll_interval: float = 15.0, testnet: bool = False):
        super().__init__(bus, session_factory, poll_interval)
        self._testnet = testnet
        self._adapters: dict[int, BinanceExecutor] = {}

    async def stop(self) -> None:
        for a in self._adapters.values():
            try:
                await a.aclose()
            except Exception:
                pass
        await super().stop()

    def _adapter(self, leader_id: int, api_key: str, api_secret: str) -> BinanceExecutor:
        if leader_id not in self._adapters:
            self._adapters[leader_id] = BinanceExecutor(
                api_key=api_key, api_secret=api_secret, testnet=self._testnet
            )
        return self._adapters[leader_id]

    async def _leaders(self):
        from sqlalchemy import select

        from app.db.models import Leader

        async with self._session_factory() as session:
            res = await session.execute(
                select(Leader).where(Leader.source == "cex_trader", Leader.is_active.is_(True))
            )
            return list(res.scalars().all())

    async def _fetch(self, leader):

        creds = decrypt_leader_credentials(leader)
        if not creds["api_key"]:
            return {}, {}, float(leader.equity_usd or 10_000.0)

        adapter = self._adapter(leader.id, creds["api_key"], creds["api_secret"])
        try:
            state = await adapter.account_state()
        except ExecutionError as e:
            log.error("cex leader poll sub=%s: %s", leader.id, e)
            return {}, {}, float(leader.equity_usd or 10_000.0)

        positions: dict[str, dict] = {}
        prices: dict[str, float] = {}
        for pos in state.positions:
            if pos.qty <= 1e-12:
                continue
            positions[pos.symbol] = {
                "szi": pos.qty if pos.side == "long" else -pos.qty,
                "side": pos.side,
            }
            prices[pos.symbol] = (await adapter.mark_price(pos.symbol)) or pos.entry_price
        return positions, prices, state.equity_usd
