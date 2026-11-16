"""Optional Twitch chat announcements (now playing, skip, refund)."""

from __future__ import annotations

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
        # fire-and-forget; we don't await here to avoid blocking the player loop
        try:
            import asyncio
            asyncio.get_event_loop().create_task(self.announce(msg))
        except RuntimeError:
            pass

    def skipped(self, title: str) -> None:
        msg = f"⏭ Трек пропущен: {title}"
        try:
            import asyncio
            asyncio.get_event_loop().create_task(self.announce(msg))
        except RuntimeError:
            pass

    def refunded(self, title: str, requester: str, reason: str) -> None:
        msg = f"↩ Возврат баллов: {title} ({requester}) — {reason}"
        try:
            import asyncio
            asyncio.get_event_loop().create_task(self.announce(msg))
        except RuntimeError:
            pass
