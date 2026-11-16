"""Twitch Helix API wrappers used by the bot.

Covers: custom reward create/get, redemption get/update, send chat message,
and EventSub subscription creation.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger("twitch_musicbot.api")

BASE = "https://api.twitch.tv/helix"


class TwitchAPIError(RuntimeError):
    """Raised on non-2xx responses from Twitch Helix."""


class TwitchAPI:
    def __init__(self, client_id: str, auth_headers_callable) -> None:
        self.client_id = client_id
        self._auth_headers = auth_headers_callable

    def _headers(self) -> dict[str, str]:
        headers = self._auth_headers()
        headers["Client-Id"] = self.client_id
        headers["Content-Type"] = "application/json"
        return headers

    # ---------------------------------------------------------------- EventSub

    def create_eventsub_subscription(
        self,
        sub_type: str,
        version: str,
        condition: dict[str, Any],
        session_id: str,
    ) -> dict[str, Any]:
        """Create an EventSub subscription linked to a WebSocket session."""
        payload = {
            "type": sub_type,
            "version": version,
            "condition": condition,
            "transport": {
                "method": "websocket",
                "session_id": session_id,
            },
        }
        resp = httpx.post(
            f"{BASE}/eventsub/subscriptions",
            headers=self._headers(),
            json=payload,
            timeout=30.0,
        )
        if resp.status_code == 409:
            # Already subscribed to this condition for this session
            logger.info("Подписка EventSub %s уже существует для сессии %s", sub_type, session_id)
            return resp.json().get("data", [{}])[0]
        if resp.status_code not in (200, 202):
            raise TwitchAPIError(
                f"Не удалось создать подписку EventSub {sub_type}: {resp.status_code} {resp.text}"
            )
        data = resp.json()["data"][0]
        logger.info("EventSub подписка создана: %s (id=%s)", sub_type, data.get("id"))
        return data

    # ---------------------------------------------------------------- rewards

    def list_rewards(self, broadcaster_id: str) -> list[dict[str, Any]]:
        resp = httpx.get(
            f"{BASE}/channel_points/custom_rewards",
            params={"broadcaster_id": broadcaster_id, "only_manageable_rewards": "true"},
            headers=self._headers(),
            timeout=30.0,
        )
        if resp.status_code != 200:
            raise TwitchAPIError(f"Не удалось получить награды: {resp.status_code} {resp.text}")
        return resp.json().get("data", [])

    def create_reward(
        self,
        broadcaster_id: str,
        title: str,
        cost: int,
        prompt: str,
        background_color: str,
        is_user_input_required: bool,
        max_per_stream: int,
        max_per_user_per_stream: int,
        global_cooldown_seconds: int,
        should_redemptions_skip_request_queue: bool,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "title": title,
            "cost": cost,
            "prompt": prompt,
            "background_color": background_color,
            "is_user_input_required": is_user_input_required,
            "should_redemptions_skip_request_queue": should_redemptions_skip_request_queue,
        }
        if max_per_stream > 0:
            body["is_max_per_stream_enabled"] = True
            body["max_per_stream"] = max_per_stream
        if max_per_user_per_stream > 0:
            body["is_max_per_user_per_stream_enabled"] = True
            body["max_per_user_per_stream"] = max_per_user_per_stream
        if global_cooldown_seconds > 0:
            body["is_global_cooldown_enabled"] = True
            body["global_cooldown_seconds"] = global_cooldown_seconds

        resp = httpx.post(
            f"{BASE}/channel_points/custom_rewards",
            params={"broadcaster_id": broadcaster_id},
            headers=self._headers(),
            json=body,
            timeout=30.0,
        )
        if resp.status_code != 200:
            raise TwitchAPIError(f"Не удалось создать награду: {resp.status_code} {resp.text}")
        return resp.json()["data"][0]

    # ------------------------------------------------------------- redemptions

    def get_redemptions(
        self,
        broadcaster_id: str,
        reward_id: str,
        status: str = "UNFULFILLED",
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "broadcaster_id": broadcaster_id,
            "reward_id": reward_id,
            "status": status,
        }
        resp = httpx.get(
            f"{BASE}/channel_points/custom_rewards/redemptions",
            params=params,
            headers=self._headers(),
            timeout=30.0,
        )
        if resp.status_code != 200:
            raise TwitchAPIError(f"Не удалось получить редемпшены: {resp.status_code} {resp.text}")
        return resp.json().get("data", [])

    def update_redemption(
        self,
        broadcaster_id: str,
        reward_id: str,
        redemption_ids: list[str],
        status: str,
    ) -> list[dict[str, Any]]:
        if not redemption_ids:
            return []
        ids_param = "&".join(f"id={rid}" for rid in redemption_ids)
        resp = httpx.patch(
            f"{BASE}/channel_points/custom_rewards/redemptions?"
            f"broadcaster_id={broadcaster_id}&reward_id={reward_id}&{ids_param}",
            headers=self._headers(),
            json={"status": status},
            timeout=30.0,
        )
        if resp.status_code != 200:
            raise TwitchAPIError(
                f"Не удалось обновить редемпшены: {resp.status_code} {resp.text}"
            )
        return resp.json().get("data", [])

    # ------------------------------------------------------------------- chat

    def send_message(
        self,
        broadcaster_id: str,
        sender_id: str,
        message: str,
    ) -> bool:
        """Send a chat message as the bot. Returns True if sent."""
        if not broadcaster_id or not sender_id or not message.strip():
            return False
        resp = httpx.post(
            f"{BASE}/chat/messages",
            headers=self._headers(),
            json={
                "broadcaster_id": broadcaster_id,
                "sender_id": sender_id,
                "message": message,
            },
            timeout=30.0,
        )
        if resp.status_code == 200:
            try:
                data = resp.json()
                return bool(data.get("data", [{}])[0].get("is_sent", False))
            except Exception:
                return False
        return False
