from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

from copyflow_core.models import SignalEvent

log = logging.getLogger(__name__)


class EventBus:
    """Minimal in-process pub/sub for signals.

    Durable enough for a modular monolith MVP: every event is persisted by the
    API layer before publication; the bus itself is at-least-once fan-out.
    Swap with Redis Streams later without changing consumers.
    """

    def __init__(self) -> None:
        self._queues: dict[int, asyncio.Queue[SignalEvent | None]] = {}
        self._lock = asyncio.Lock()

    async def publish(self, event: SignalEvent) -> None:
        async with self._lock:
            queues = list(self._queues.values())
        for q in queues:
            await q.put(event)
        log.info("bus.publish signal=%s leader=%s %s %s %s",
                 event.id, event.leader_id, event.action.value,
                 event.instrument.key(), event.side.value)

    async def subscribe(self, subscriber_id: int) -> AsyncIterator[SignalEvent | None]:
        q: asyncio.Queue[SignalEvent | None] = asyncio.Queue(maxsize=1000)
        async with self._lock:
            self._queues[subscriber_id] = q
        try:
            while True:
                item = await q.get()
                yield item
                if item is None:
                    return
        finally:
            async with self._lock:
                self._queues.pop(subscriber_id, None)

    async def close_all(self) -> None:
        async with self._lock:
            queues = list(self._queues.values())
        for q in queues:
            await q.put(None)
