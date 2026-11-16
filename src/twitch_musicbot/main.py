"""Main orchestrator for twitch-musicbot.

Wires together Twitch OAuth, EventSub WebSocket, the request queue,
headless mpv playback, and Kopuz pause/resume via kopuzctl.
"""

from __future__ import annotations

import asyncio
import logging
import re
import sys
from pathlib import Path

from .api import TwitchAPI, TwitchAPIError
from .auth import TwitchAuth
from .chat import Chat
from .config import Config, resolve_state_dir
from .eventsub import EventSubClient
from .kopuz import Kopuz
from .player import Player, PlayerError, Track
from .queue import Queue, QueuedTrack
from .state import PauseState

logger = logging.getLogger("twitch_musicbot.main")

YOUTUBE_URL_RE = re.compile(
    r"^(https?://)?(www\.)?(youtube\.com/watch\?v=|youtu\.be/|youtube\.com/embed/|youtube\.com/v/)[A-Za-z0-9_-]{11}"
)


class MusicBot:
    def __init__(self, config: Config) -> None:
        self.cfg = config
        self.state_dir = resolve_state_dir(config)

        # Components
        self.auth = TwitchAuth(
            config.client_id,
            config.client_secret,
            config.scopes,
            config.token_file,
        )
        self.api = TwitchAPI(config.client_id, self.auth.auth_headers)
        self.kopuz = Kopuz(config.kopuzctl)
        self.player = Player(
            config.mpv,
            config.yt_dlp,
            config.yt_dlp_timeout_seconds,
            config.mpv_timeout_seconds,
        )
        self.pause_state = PauseState(self.state_dir)
        self.queue = Queue(config)

        # Queues / tasks
        self.redemption_queue: asyncio.Queue[dict] = asyncio.Queue()
        self._playback_task: asyncio.Task | None = None
        self._processor_task: asyncio.Task | None = None
        self._eventsub_task: asyncio.Task | None = None
        self._eventsub: EventSubClient | None = None
        self.chat: Chat | None = None

        # Runtime
        self._playing = False
        self._bot_user_id: str | None = None
        self._reward_id: str | None = None
        self._running = False

    # ------------------------------------------------------------------ startup

    async def startup(self) -> None:
        logger.info("=== Twitch Music Bot старт ===")

        # 1. Auth
        if not self.auth.load():
            logger.info("Нет сохранённых токенов — запускаю Device Code Flow.")
            self.auth.authorize_interactive()
        else:
            self.auth.ensure_valid()

        info = self.auth.validate()
        if info:
            self._bot_user_id = info.get("user_id") or info.get("login")
            logger.info("Авторизован как %s", self._bot_user_id)

        # 2. Fail-open: if we previously paused Kopuz and are restarting,
        #    resume it so the stream isn't left silent.
        if self.pause_state.get():
            logger.warning("Обнаружен флаг paused_by_us после перезапуска — снимаю паузу с Kopuz.")
            self.kopuz.play()
            self.pause_state.clear()

        # 3. Reward: find existing or create
        self._reward_id = await asyncio.get_event_loop().run_in_executor(
            None, self._ensure_reward
        )
        logger.info("Награда: %s", self._reward_id or "(не найдена / не создана)")

        # 4. Resume persisted queue (if any)
        queue_file = self.state_dir / "queue.json"
        if queue_file.exists():
            self.queue = Queue.load(queue_file, self.cfg)
            logger.info("Очередь восстановлена: %d треков", self.queue.count())

        # 5. Check tooling
        missing = []
        for name, path in (
            ("mpv", self.cfg.mpv),
            ("yt-dlp", self.cfg.yt_dlp),
            ("kopuzctl", self.cfg.kopuzctl),
        ):
            if not self.player.tool_available(path):
                missing.append(f"{name} ({path})")
        if missing:
            logger.warning("Не найдены инструменты: %s. bot будет работать некорректно.", ", ".join(missing))

    def _ensure_reward(self) -> str | None:
        """Find the bot's reward by title, or create it. Returns reward ID."""
        broadcaster = self.cfg.broadcaster_user_id
        if not broadcaster:
            logger.error("broadcaster_user_id не задан в конфиге.")
            return None

        try:
            rewards = self.api.list_rewards(broadcaster)
        except TwitchAPIError as e:
            logger.error("Не удалось получить список наград: %s", e)
            return None

        for r in rewards:
            if r.get("title") == self.cfg.reward_title:
                logger.info("Награда уже существует: %s", r["id"])
                return r["id"]

        try:
            created = self.api.create_reward(
                broadcaster_id=broadcaster,
                title=self.cfg.reward_title,
                cost=self.cfg.reward_cost,
                prompt=self.cfg.reward_prompt,
                background_color=self.cfg.reward_background_color,
                is_user_input_required=self.cfg.reward_is_user_input_required,
                max_per_stream=self.cfg.reward_max_per_stream,
                max_per_user_per_stream=self.cfg.reward_max_per_user_per_stream,
                global_cooldown_seconds=self.cfg.reward_global_cooldown_seconds,
                should_redemptions_skip_request_queue=self.cfg.reward_should_redemptions_skip_request_queue,
            )
            rid = created["id"]
            logger.info("Создана новая награда: %s (цена %s баллов)", rid, self.cfg.reward_cost)
            return rid
        except TwitchAPIError as e:
            logger.error("Не удалось создать награду: %s", e)
            return None

    # ---------------------------------------------------------------- redemption processing

    async def _process_redemptions(self) -> None:
        """Consume redemptions from the WS queue: validate, enqueue, refund on error."""
        while self._running:
            try:
                redemption = await asyncio.wait_for(self.redemption_queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue

            await self._handle_redemption(redemption)

    async def _handle_redemption(self, redemption: dict) -> None:
        rid = redemption.get("id", "")
        reward_id = redemption.get("reward", {}).get("id", "")
        if self._reward_id and reward_id != self._reward_id:
            return  # ignore redemptions for other rewards

        user_id = redemption.get("user_id", "")
        user_login = redemption.get("user_login", "")
        user_name = redemption.get("user_name", "")
        user_input = redemption.get("user_input", "")

        # Validate input
        if not user_input or not YOUTUBE_URL_RE.match(user_input.strip()):
            reason = "Нужен прямой URL на YouTube (youtube.com/watch?v=... или youtu.be/...)."
            await self._refund_redemption(rid, reward_id, user_name, reason)
            return

        url = user_input.strip()
        if not url.startswith("http"):
            url = "https://" + url

        # Capacity checks
        ok, reason = self.queue.can_add(user_id, url)
        if not ok:
            await self._refund_redemption(rid, reward_id, user_name, reason)
            return

        # Resolve track via yt-dlp
        try:
            track = await asyncio.get_event_loop().run_in_executor(
                None, self.player.resolve, url
            )
        except PlayerError as e:
            await self._refund_redemption(rid, reward_id, user_name, str(e))
            return

        if track is None:
            await self._refund_redemption(
                rid, reward_id, user_name, "Трек недоступен или это трансляция."
            )
            return

        # Duration limit
        if (
            self.cfg.max_track_seconds > 0
            and track.duration_seconds is not None
            and track.duration_seconds > self.cfg.max_track_seconds
        ):
            await self._refund_redemption(
                rid,
                reward_id,
                user_name,
                f"Трек слишком длинный ({track.duration_seconds // 60} мин, лимит {self.cfg.max_track_seconds // 60} мин).",
            )
            return

        # Enqueue
        item = QueuedTrack(
            track_title=track.title,
            track_url=track.url,
            duration_seconds=track.duration_seconds,
            requester_id=user_id,
            requester_login=user_login,
            requester_name=user_name,
            redemption_id=rid,
        )
        pos = self.queue.add(item)
        self.queue.save(self.state_dir / "queue.json")

        # Chat announce
        chat_msg = f"✅ {user_name} заказал: {track.title} (очередь #{pos})"
        self._schedule_chat(chat_msg)

        # If nothing is playing, start playback (which pauses Kopuz)
        if not self._playing:
            asyncio.create_task(self._playback_loop())

    async def _refund_redemption(
        self, redemption_id: str, reward_id: str, user_name: str, reason: str
    ) -> None:
        if not self.cfg.auto_refund_on_error:
            return
        if not self._reward_id or not self.cfg.broadcaster_user_id:
            return
        try:
            await asyncio.get_event_loop().run_in_executor(
                None,
                self.api.update_redemption,
                self.cfg.broadcaster_user_id,
                self._reward_id,
                [redemption_id],
                "CANCELED",
            )
            logger.info("Редемпшен %s отменён, баллы возвращены: %s", redemption_id, reason)
            self._schedule_chat(f"↩ {user_name}, баллы возвращены: {reason}")
        except TwitchAPIError as e:
            logger.error("Не удалось отменить редемпшен %s: %s", redemption_id, e)

    # ---------------------------------------------------------------- playback loop

    async def _playback_loop(self) -> None:
        """Play queued tracks one by one. Pauses Kopuz while any track plays."""
        self._playing = True
        self._playback_task = asyncio.current_task()
        if self.cfg.pause_kopuz_during_request:
            self.kopuz.pause()
            self.pause_state.set(True)

        try:
            while self._running:
                item = self.queue.pop_next()
                if item is None:
                    break  # queue empty

                track = Track(
                    url=item.track_url,
                    title=item.track_title,
                    duration_seconds=item.duration_seconds,
                    source="queue",
                    requester=item.requester_name,
                    requester_login=item.requester_login,
                )

                self._schedule_chat(f"▶ Сейчас играет: {track.title} — заказал {item.requester_name}")

                try:
                    code = await asyncio.get_event_loop().run_in_executor(
                        None, self.player.play, track
                    )
                except PlayerError as e:
                    logger.error("Ошибка воспроизведения %s: %s", track.title, e)
                    await self._refund_redemption(
                        item.redemption_id,
                        self._reward_id or "",
                        item.requester_name,
                        str(e),
                    )
                    continue  # try next in queue

                # Track finished naturally
                # Mark redemption fulfilled
                if item.redemption_id and self._reward_id:
                    await asyncio.get_event_loop().run_in_executor(
                        None,
                        self.api.update_redemption,
                        self.cfg.broadcaster_user_id,
                        self._reward_id,
                        [item.redemption_id],
                        "FULFILLED",
                    )
                self.queue.save(self.state_dir / "queue.json")

                if self.queue.is_empty():
                    break  # exit loop, will resume Kopuz below
        finally:
            self._playing = False

        # Queue empty — resume Kopuz after debounce
        if self.cfg.pause_kopuz_during_request and self.pause_state.get():
            await asyncio.sleep(self.cfg.resume_debounce_seconds)
            # Double-check nothing was added while we slept
            if self.queue.is_empty() and self.pause_state.get():
                self.kopuz.play()
                self.pause_state.clear()
                self.queue.save(self.state_dir / "queue.json")
                self._schedule_chat("▶ Очередь пуста, локальная музыка продолжена.")

    def _schedule_chat(self, message: str) -> None:
        if self.chat and self.cfg.announce_in_chat:
            # fire-and-forget
            try:
                asyncio.get_event_loop().create_task(self.chat.announce(message))
            except RuntimeError:
                pass

    # ---------------------------------------------------------------- eventsub wiring

    async def _on_eventsub_redemption(self, event: dict) -> None:
        await self.redemption_queue.put(event)

    async def run(self) -> None:
        self._running = True
        await self.startup()

        self.chat = Chat(self.api, self.cfg.broadcaster_user_id, self._bot_user_id or "", self.cfg.announce_in_chat)

        # Start redemption processor
        self._processor_task = asyncio.create_task(self._process_redemptions())

        # Start EventSub client
        self._eventsub = EventSubClient(
            access_token_getter=lambda: self.auth.access_token or "",
            broadcaster_user_id=self.cfg.broadcaster_user_id,
            reward_id=self._reward_id,
            on_redemption=self._on_eventsub_redemption,
        )
        self._eventsub_task = asyncio.create_task(self._eventsub.run())

        logger.info("Бот готов. Ожидаю заказы...")
        try:
            while self._running:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass
        finally:
            await self.shutdown()

    async def shutdown(self) -> None:
        self._running = False
        for t in (self._eventsub_task, self._processor_task, self._playback_task):
            if t and not t.done():
                t.cancel()
        if self._eventsub:
            await self._eventsub.stop()
        self.player.stop()
        self.queue.save(self.state_dir / "queue.json")
        logger.info("Бот остановлен.")
