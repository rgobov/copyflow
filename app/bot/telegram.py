from __future__ import annotations

import logging

from app.config import settings

log = logging.getLogger(__name__)


class TelegramNotifier:
    """aiogram v3 wrapper. Degrades to logging when no token configured."""

    def __init__(self) -> None:
        self._bot = None
        self._chat_ids: list[str] = []
        if settings.telegram_bot_token:
            try:
                from aiogram import Bot

                self._bot = Bot(token=settings.telegram_bot_token)
            except Exception:
                log.exception("telegram bot init failed; notifications disabled")

    def set_chat_ids(self, chat_ids: list[str]) -> None:
        self._chat_ids = chat_ids

    async def notify(self, text: str) -> None:
        if not self._bot or not self._chat_ids:
            log.info("telegram(skip): %s", text)
            return
        for chat_id in self._chat_ids:
            try:
                await self._bot.send_message(chat_id, text)
            except Exception:
                log.exception("telegram send failed chat=%s", chat_id)

    async def notify_fill(
        self, mode: str, leader: str, symbol: str, side: str, notional: float, price: float
    ) -> None:
        icon = "🟢" if side == "buy" else "🔴"
        await self.notify(
            f"{icon} [{mode}] {leader}: {side.upper()} {symbol} "
            f"${notional:.0f} @ {price:.4f}"
        )
