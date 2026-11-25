from __future__ import annotations

import asyncio
import logging

from copyflow_core.models import Action, Instrument, Side, SignalEvent

log = logging.getLogger(__name__)

EPS = 1e-12


class LeaderPositionPoller:
    """Base for leader sources that expose position snapshots (HL wallets, CEX
    accounts with read-only keys). Snapshot diff -> canonical signals.

    size_frac is notional-based: (change in position * mark price) / leader equity.
    """

    venue: str = "abstract"

    def __init__(self, bus, session_factory, poll_interval: float = 15.0):
        self._bus = bus
        self._session_factory = session_factory
        self._interval = poll_interval
        self._snapshots: dict[int, dict[str, dict]] = {}
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name=f"{type(self).__name__}")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _run(self) -> None:
        while True:
            try:
                await self.poll_once()
            except Exception:
                log.exception("%s poll failed", type(self).__name__)
            await asyncio.sleep(self._interval)

    # ---- to implement in subclasses ----
    async def _leaders(self) -> list:
        raise NotImplementedError

    async def _fetch(self, leader) -> tuple[dict[str, dict], dict[str, float], float]:
        """Returns (positions {coin: {szi signed, side}}, prices {coin: px}, equity_usd)."""
        raise NotImplementedError

    def _instrument(self, coin: str) -> Instrument:
        return Instrument(venue=self.venue, symbol=coin)

    # ---- core ----
    async def poll_once(self) -> list[SignalEvent]:
        events: list[SignalEvent] = []
        for leader in await self._leaders():
            positions, prices, equity = await self._fetch(leader)
            prev = self._snapshots.get(leader.id, {})
            events.extend(
                await self._diff(leader.id, equity, prices, prev, positions)
            )
            self._snapshots[leader.id] = positions
        return events

    async def _diff(self, leader_id: int, equity: float, prices: dict[str, float],
                    prev: dict[str, dict], current: dict[str, dict]) -> list[SignalEvent]:
        events: list[SignalEvent] = []

        def frac(notional_change: float) -> float:
            return round(notional_change / equity, 6) if equity > 0 else 0.0

        for coin, cur in current.items():
            szi, price = abs(cur["szi"]), prices.get(coin) or 0.0
            notional = szi * price
            old = prev.get(coin)
            side = Side(cur["side"])
            old_szi = abs(old["szi"]) if old else 0.0

            if old is None:
                action, f = Action.OPEN, frac(notional)
            elif cur["side"] != old["side"]:
                action, f = Action.FLIP, frac(notional)
            elif szi > old_szi + EPS:
                action, f = Action.INCREASE, frac((szi - old_szi) * price)
            elif szi < old_szi - EPS:
                action, f = Action.REDUCE, frac((old_szi - szi) * price)
            else:
                continue
            events.append(self._event(leader_id, coin, action, side, f))

        for coin, old in prev.items():
            if coin not in current:
                events.append(
                    self._event(leader_id, coin, Action.CLOSE, Side(old["side"]), 1.0)
                )

        for ev in events:
            await self._bus.publish(ev)
        return events

    def _event(self, leader_id: int, coin: str, action: Action, side: Side,
               frac: float) -> SignalEvent:
        return SignalEvent(
            leader_id=leader_id,
            instrument=self._instrument(coin),
            action=action,
            side=side,
            size_frac=max(frac, 0.0) if action != Action.CLOSE else 1.0,
        )
