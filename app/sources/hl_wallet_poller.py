from __future__ import annotations

import asyncio
import logging

import httpx
from copyflow_core.models import Action, Instrument, Side, SignalEvent

log = logging.getLogger(__name__)

HL_API = "https://api.hyperliquid.xyz"


class HlWalletPoller:
    """Polls a Hyperliquid wallet's perp positions and emits diff-based signals.

    clearninghouseState -> positions snapshot; snapshot diff produces
    open / increase / reduce / flip / close events proportional to leader equity.
    """

    def __init__(
        self,
        bus,
        session_factory,
        poll_interval: float = 7.0,
        api_url: str = HL_API,
    ):
        self._bus = bus
        self._session_factory = session_factory
        self._interval = poll_interval
        self._api_url = api_url.rstrip("/")
        self._snapshots: dict[int, dict[str, dict]] = {}  # leader_id -> coin -> {szi, side}
        self._client = httpx.AsyncClient(timeout=10.0)
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="hl-wallet-poller")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        await self._client.aclose()

    async def _run(self) -> None:
        while True:
            try:
                await self.poll_once()
            except Exception:
                log.exception("hl poll failed")
            await asyncio.sleep(self._interval)

    async def _leaders(self):
        from sqlalchemy import select

        from app.db.models import Leader

        async with self._session_factory() as session:
            res = await session.execute(
                select(Leader).where(Leader.source == "hl_wallet", Leader.is_active.is_(True))
            )
            return list(res.scalars().all())

    async def _fetch_positions(self, wallet: str) -> dict[str, dict]:
        r = await self._client.post(
            f"{self._api_url}/info", json={"type": "clearinghouseState", "user": wallet}
        )
        r.raise_for_status()
        state = r.json()
        out: dict[str, dict] = {}
        for item in state.get("assetPositions", []):
            pos = item.get("position", {})
            try:
                szi = float(pos.get("szi", 0))
            except (TypeError, ValueError):
                continue
            if abs(szi) < 1e-12:
                continue
            out[pos.get("coin", "")] = {"szi": szi, "side": "long" if szi > 0 else "short"}
        return out

    async def poll_once(self) -> list[SignalEvent]:
        leaders = await self._leaders()
        events: list[SignalEvent] = []
        for leader in leaders:
            wallet = (leader.config or {}).get("wallet")
            if not wallet:
                continue
            equity = float(leader.equity_usd or 10_000.0)
            current = await self._fetch_positions(wallet)
            prev = self._snapshots.get(leader.id, {})
            events.extend(await self._diff(leader.id, equity, prev, current))
            self._snapshots[leader.id] = current
        return events

    async def _diff(
        self, leader_id: int, equity: float, prev: dict[str, dict], current: dict[str, dict]
    ) -> list[SignalEvent]:
        events: list[SignalEvent] = []
        for coin, cur in current.items():
            old = prev.get(coin)
            side = Side(cur["side"])
            szi, old_szi = abs(cur["szi"]), abs(old["szi"]) if old else 0.0
            if old is None:
                action = Action.OPEN
            elif cur["side"] != old["side"]:
                action = Action.FLIP
            elif szi > old_szi + 1e-12:
                action = Action.INCREASE
            elif szi < old_szi - 1e-12:
                action = Action.REDUCE
            else:
                continue

            if action == Action.REDUCE:
                frac = (old_szi - szi) / old_szi
            elif action in (Action.OPEN, Action.FLIP):
                frac = szi / equity
            else:
                frac = (szi - old_szi) / equity

            events.append(
                SignalEvent(
                    leader_id=leader_id,
                    instrument=Instrument(venue="hyperliquid", symbol=coin),
                    action=action,
                    side=side,
                    size_frac=round(frac, 6),
                )
            )

        for coin, old in prev.items():
            if coin not in current:
                events.append(
                    SignalEvent(
                        leader_id=leader_id,
                        instrument=Instrument(venue="hyperliquid", symbol=coin),
                        action=Action.CLOSE,
                        side=Side(old["side"]),
                        size_frac=1.0,
                    )
                )

        for ev in events:
            await self._bus.publish(ev)
        return events
