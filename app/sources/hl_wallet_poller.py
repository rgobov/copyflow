from __future__ import annotations

import logging

import httpx

from app.sources.base import LeaderPositionPoller

log = logging.getLogger(__name__)

HL_API = "https://api.hyperliquid.xyz"


class HlWalletPoller(LeaderPositionPoller):
    """Polls a Hyperliquid wallet's perp positions and emits diff-based signals.

    clearinghouseState -> positions snapshot; snapshot diff produces
    open / increase / reduce / flip / close events with notional-based
    size_frac (position * mid price / leader equity).
    """

    venue = "hyperliquid"

    def __init__(self, bus, session_factory, poll_interval: float = 7.0, api_url: str = HL_API):
        super().__init__(bus, session_factory, poll_interval)
        self._api_url = api_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=10.0)

    async def stop(self) -> None:
        await self._client.aclose()
        await super().stop()

    async def _leaders(self):
        from sqlalchemy import select

        from app.db.models import Leader

        async with self._session_factory() as session:
            res = await session.execute(
                select(Leader).where(Leader.source == "hl_wallet", Leader.is_active.is_(True))
            )
            return list(res.scalars().all())

    async def _fetch(self, leader):
        wallet = (leader.config or {}).get("wallet")
        if not wallet:
            return {}, {}, float(leader.equity_usd or 10_000.0)
        state = (await self._info({"type": "clearinghouseState", "user": wallet}))
        mids = (await self._info({"type": "allMids"})).get("mids", {})
        equity = float(state.get("marginSummary", {}).get("accountValue", 0) or leader.equity_usd or 10_000.0)

        positions: dict[str, dict] = {}
        prices: dict[str, float] = {}
        for item in state.get("assetPositions", []):
            pos = item.get("position", {})
            try:
                szi = float(pos.get("szi", 0))
            except (TypeError, ValueError):
                continue
            if abs(szi) < 1e-12:
                continue
            coin = pos.get("coin", "")
            positions[coin] = {"szi": szi, "side": "long" if szi > 0 else "short"}
            mid = mids.get(coin)
            prices[coin] = float(mid) if mid else 0.0
        return positions, prices, equity

    async def _info(self, payload: dict) -> dict:
        r = await self._client.post(f"{self._api_url}/info", json=payload)
        r.raise_for_status()
        return r.json()
