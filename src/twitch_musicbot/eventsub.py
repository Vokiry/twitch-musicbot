"""Twitch EventSub WebSocket client.

Connects to wss://eventsub.wss.twitch.tv/ws, subscribes to
channel.channel_points_custom_reward_redemption.add and dispatches
incoming redemptions to a callback. Handles welcome/keepalive/ping/reconnect
and automatic re-subscription on reconnect.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Callable, Awaitable

import websockets

logger = logging.getLogger("twitch_musicbot.eventsub")

EVENTSUB_WS_URL = "wss://eventsub.wss.twitch.tv/ws"

REDEMPTION_TYPE = "channel.channel_points_custom_reward_redemption.add"
REDEMPTION_VERSION = "1"

RedemptionCallback = Callable[[dict], Awaitable[None]]


class EventSubClient:
    def __init__(
        self,
        access_token_getter: Callable[[], str],
        broadcaster_user_id: str,
        reward_id: str | None,
        on_redemption: RedemptionCallback,
    ) -> None:
        self._token_getter = access_token_getter
        self._broadcaster_user_id = broadcaster_user_id
        self._reward_id = reward_id
        self._on_redemption = on_redemption
        self._session_id: str | None = None
        self._ws = None  # type: ignore[assignment]
        self._running = False
        self._subscriptions: list[dict] = []

    async def run(self) -> None:
        self._running = True
        backoff = 1.0
        while self._running:
            try:
                async with websockets.connect(EVENTSUB_WS_URL, ping_interval=None) as ws:
                    self._ws = ws
                    backoff = 1.0
                    await self._handle_connection(ws)
            except websockets.ConnectionClosedError as exc:
                logger.warning("EventSub WS closed (%s), reconnecting in %.0fs", exc.code, backoff)
                await asyncio.sleep(min(backoff, 30.0))
                backoff = min(backoff * 2, 30.0)
            except Exception:  # noqa: BLE001
                logger.exception("EventSub WS error, reconnecting in %.0fs", backoff)
                await asyncio.sleep(min(backoff, 30.0))
                backoff = min(backoff * 2, 30.0)

    async def stop(self) -> None:
        self._running = False
        if self._ws is not None:
            await self._ws.close()

    async def _handle_connection(self, ws) -> None:
        # First message must be session_welcome within ~10s
        welcome = await asyncio.wait_for(ws.recv(), timeout=15.0)
        msg = json.loads(welcome)
        if msg.get("metadata", {}).get("message_type") != "session_welcome":
            raise RuntimeError(f"Expected welcome, got {msg}")
        self._session_id = msg["payload"]["session"]["id"]
        logger.info("EventSub session connected: %s", self._session_id)

        await self._subscribe_all(ws)

        # Loop reading messages
        async for raw in ws:
            msg = json.loads(raw)
            mtype = msg.get("metadata", {}).get("message_type")
            if mtype == "notification":
                await self._on_notification(msg)
            elif mtype == "session_keepalive":
                logger.debug("EventSub keepalive")
            elif mtype == "session_reconnect":
                url = msg["payload"]["session"].get("reconnect_url")
                logger.info("EventSub reconnect requested: %s", url)
                return  # exit to outer run() which reconnects (subscriptions persist on reconnect URL)
            elif mtype == "revocation":
                sub = msg.get("subscription", {})
                logger.warning(
                    "EventSub revocation: type=%s status=%s",
                    sub.get("type"),
                    sub.get("status"),
                )
            elif mtype == "session_close" or mtype == "close":
                logger.info("EventSub close message")
                return

    async def _subscribe_all(self, ws) -> None:
        self._subscriptions = [
            {
                "type": REDEMPTION_TYPE,
                "version": REDEMPTION_VERSION,
                "condition": {"broadcaster_user_id": self._broadcaster_user_id},
                "transport": {"method": "websocket", "session_id": self._session_id},
            }
        ]
        if self._reward_id:
            self._subscriptions[0]["condition"]["reward_id"] = self._reward_id

        for sub in self._subscriptions:
            await ws.send(json.dumps(sub))
            logger.info("Subscribed to %s", sub["type"])

    async def _on_notification(self, msg: dict) -> None:
        sub = msg.get("subscription", {})
        event = msg.get("event", {})
        if sub.get("type") == REDEMPTION_TYPE:
            await self._on_redemption(event)
