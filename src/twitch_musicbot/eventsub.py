"""Twitch EventSub WebSocket client.

Connects to wss://eventsub.wss.twitch.tv/ws, listens for
channel.channel_points_custom_reward_redemption.add notifications.

IMPORTANT: The EventSub WebSocket server strictly prohibits inbound client
messages (except websocket pongs). Subscriptions must be registered via the
Helix REST API using the session_id received in session_welcome.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Awaitable, Callable

import websockets

logger = logging.getLogger("twitch_musicbot.eventsub")

EVENTSUB_WS_URL = "wss://eventsub.wss.twitch.tv/ws"
REDEMPTION_TYPE = "channel.channel_points_custom_reward_redemption.add"

RedemptionCallback = Callable[[dict], Awaitable[None]]
SessionWelcomeCallback = Callable[[str], Awaitable[None]]


class EventSubClient:
    def __init__(
        self,
        on_session_welcome: SessionWelcomeCallback,
        on_redemption: RedemptionCallback,
    ) -> None:
        self._on_session_welcome = on_session_welcome
        self._on_redemption = on_redemption
        self._session_id: str | None = None
        self._ws: websockets.WebSocketClientProtocol | None = None
        self._running = False
        self._reconnect_url: str | None = None

    async def run(self) -> None:
        self._running = True
        backoff = 1.0

        while self._running:
            url = self._reconnect_url or EVENTSUB_WS_URL
            is_reconnecting = bool(self._reconnect_url)
            self._reconnect_url = None

            try:
                logger.info("Подключение к EventSub WebSocket (%s)...", url)
                async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
                    self._ws = ws
                    backoff = 1.0
                    await self._handle_connection(ws, is_reconnecting)
            except websockets.ConnectionClosedError as exc:
                if self._running:
                    logger.warning(
                        "EventSub соединение разорвано (%s), переподключение через %.0fs",
                        exc.code,
                        backoff,
                    )
                    await asyncio.sleep(min(backoff, 30.0))
                    backoff = min(backoff * 2, 30.0)
            except asyncio.CancelledError:
                break
            except Exception:  # noqa: BLE001
                if self._running:
                    logger.exception(
                        "Ошибка EventSub WebSocket, переподключение через %.0fs",
                        backoff,
                    )
                    await asyncio.sleep(min(backoff, 30.0))
                    backoff = min(backoff * 2, 30.0)

    async def stop(self) -> None:
        self._running = False
        if self._ws is not None:
            await self._ws.close()

    async def _handle_connection(
        self, ws: websockets.WebSocketClientProtocol, is_reconnecting: bool
    ) -> None:
        # First message must be session_welcome within 15 seconds
        raw_welcome = await asyncio.wait_for(ws.recv(), timeout=15.0)
        welcome = json.loads(raw_welcome)
        mtype = welcome.get("metadata", {}).get("message_type")
        if mtype != "session_welcome":
            raise RuntimeError(f"Ожидался session_welcome, получено: {welcome}")

        self._session_id = welcome["payload"]["session"]["id"]
        logger.info("EventSub сессия подключена: id=%s", self._session_id)

        # If this is a fresh connection (not reconnect_url), register subscriptions via Helix
        if not is_reconnecting:
            await self._on_session_welcome(self._session_id)

        # Process stream of server notifications — NEVER send text frames to ws!
        async for raw in ws:
            msg = json.loads(raw)
            metadata = msg.get("metadata", {})
            mtype = metadata.get("message_type")

            if mtype == "notification":
                sub = msg.get("subscription", {})
                event = msg.get("event", {})
                if sub.get("type") == REDEMPTION_TYPE:
                    await self._on_redemption(event)
            elif mtype == "session_keepalive":
                logger.debug("EventSub keepalive")
            elif mtype == "session_reconnect":
                self._reconnect_url = msg["payload"]["session"].get("reconnect_url")
                logger.info("EventSub запросил reconnect: %s", self._reconnect_url)
                return
            elif mtype == "revocation":
                sub = msg.get("subscription", {})
                logger.warning(
                    "EventSub подписка отозвана: type=%s, status=%s",
                    sub.get("type"),
                    sub.get("status"),
                )
            elif mtype in ("session_close", "close"):
                logger.info("EventSub закрыл сессию")
                return
