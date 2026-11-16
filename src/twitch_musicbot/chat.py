"""Twitch chat announcements (now playing, skip, refund).

Fire-and-forget: schedules a task on the running event loop and executes
the blocking network call in an executor to avoid stalling the loop.
"""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger("twitch_musicbot.chat")


class Chat:
    def __init__(self, api, broadcaster_id: str, bot_user_id: str, enabled: bool) -> None:
        self.api = api
        self.broadcaster_id = broadcaster_id
        self.bot_user_id = bot_user_id
        self.enabled = enabled

    async def announce(self, message: str) -> None:
        if not self.enabled or not self.broadcaster_id or not self.bot_user_id:
            return
        try:
            loop = asyncio.get_running_loop()
            sent = await loop.run_in_executor(
                None,
                self.api.send_message,
                self.broadcaster_id,
                self.bot_user_id,
                message,
            )
            if not sent:
                logger.debug("Сообщение в чат не отправлено: %s", message)
        except Exception:  # noqa: BLE001
            logger.exception("Ошибка при отправке сообщения в чат")

    def now_playing(self, title: str, requester: str, position: int | None = None) -> None:
        if position:
            msg = f"▶ Сейчас играет: {title} — заказал {requester} (в очереди ещё {position})"
        else:
            msg = f"▶ Сейчас играет: {title} — заказал {requester}"
        self._fire(msg)

    def skipped(self, title: str) -> None:
        self._fire(f"⏭ Трек пропущен: {title}")

    def refunded(self, title: str, requester: str, reason: str) -> None:
        self._fire(f"↩ Возврат баллов: {title} ({requester}) — {reason}")

    def _fire(self, message: str) -> None:
        """Schedule announce() on the running event loop."""
        if not self.enabled:
            return
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self.announce(message))
        except RuntimeError:
            logger.debug("Нет активного цикла событий, сообщение пропущено: %s", message)
