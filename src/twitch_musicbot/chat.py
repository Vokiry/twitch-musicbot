"""Optional Twitch chat announcements (now playing, skip, refund).

Fire-and-forget: schedules a coroutine on the running event loop.
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
        if not self.enabled:
            return
        try:
            sent = self.api.send_message(
                self.broadcaster_id,
                self.bot_user_id,
                message,
            )
            if not sent:
                logger.warning("Чат-сообщение не доставлено: %s", message)
        except Exception:  # noqa: BLE001
            logger.exception("Не удалось отправить сообщение в чат")

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
        """Schedule announce() on the running event loop, if any."""
        if not self.enabled:
            return
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self.announce(message))
        except RuntimeError:
            # No running loop — message is dropped (acceptable for fire-and-forget)
            logger.debug("Нет цикла событий, сообщение в чат пропущено: %s", message)
