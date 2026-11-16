"""Main orchestrator for twitch-musicbot.

Wires together Twitch OAuth, EventSub WebSocket, the request queue,
headless mpv playback, and Kopuz pause/resume via kopuzctl.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from .api import TwitchAPI, TwitchAPIError
from .auth import TwitchAuth
from .chat import Chat
from .config import Config, resolve_state_dir
from .eventsub import EventSubClient
from .kopuz import Kopuz
from .player import Player, PlayerError, Track, extract_youtube_video_id
from .queue import Queue, QueuedTrack
from .state import PauseState

logger = logging.getLogger("twitch_musicbot.main")


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
        logger.info("=== Запуск Twitch Music Bot ===")

        # 1. Auth
        if not self.auth.load():
            logger.info("Нет сохранённых токенов — запуск Device Code Flow...")
            self.auth.authorize_interactive()
        else:
            self.auth.ensure_valid()

        info = self.auth.validate()
        if info:
            self._bot_user_id = info.get("user_id") or info.get("login")
            logger.info("Авторизован аккаунт Twitch: %s", self._bot_user_id)

        # 2. Fail-open: if we previously paused Kopuz and crashed/restarted,
        #    resume it so the stream isn't left silent.
        if self.pause_state.get():
            logger.warning("Обнаружен флаг paused_by_us после перезапуска — снимаю Kopuz с паузы.")
            self.kopuz.play()
            self.pause_state.clear()

        # 3. Reward: find existing or create
        self._reward_id = await asyncio.get_event_loop().run_in_executor(
            None, self._ensure_reward
        )
        logger.info("ID награды за баллы: %s", self._reward_id or "(не найдена / не создана)")

        # 4. Resume persisted queue (if any)
        queue_file = self.state_dir / "queue.json"
        if queue_file.exists():
            self.queue = Queue.load(queue_file, self.cfg)
            logger.info("Очередь восстановлена с диска: %d треков", self.queue.count())

        # 5. Check tooling availability
        missing = []
        for name, path in (
            ("mpv", self.cfg.mpv),
            ("yt-dlp", self.cfg.yt_dlp),
            ("kopuzctl", self.cfg.kopuzctl),
        ):
            if not self.player.tool_available(path):
                missing.append(f"{name} ({path})")
        if missing:
            logger.warning("Не найдены исполняемые файлы: %s. Проверьте пути в config.json.", ", ".join(missing))

    def _ensure_reward(self) -> str | None:
        """Find the bot's reward by title, or create it. Returns reward ID."""
        broadcaster = self.cfg.broadcaster_user_id
        if not broadcaster:
            logger.error("broadcaster_user_id не задан в конфигурации.")
            return None

        try:
            rewards = self.api.list_rewards(broadcaster)
        except TwitchAPIError as e:
            logger.error("Не удалось получить список наград: %s", e)
            return None

        for r in rewards:
            if r.get("title") == self.cfg.reward_title:
                logger.info("Награда '%s' найдена: %s", self.cfg.reward_title, r["id"])
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
            logger.info("Создана новая награда '%s': %s (цена %d баллов)", self.cfg.reward_title, rid, self.cfg.reward_cost)
            return rid
        except TwitchAPIError as e:
            logger.error("Не удалось создать награду в Twitch: %s", e)
            return None

    # ---------------------------------------------------------------- EventSub registration

    async def _on_eventsub_welcome(self, session_id: str) -> None:
        """Called when EventSub sends session_welcome. Registers the subscription via Helix REST API."""
        cond = {"broadcaster_user_id": self.cfg.broadcaster_user_id}
        if self._reward_id:
            cond["reward_id"] = self._reward_id

        await asyncio.get_event_loop().run_in_executor(
            None,
            self.api.create_eventsub_subscription,
            "channel.channel_points_custom_reward_redemption.add",
            "1",
            cond,
            session_id,
        )

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
            return  # ignore redemptions for other channel point rewards

        user_id = redemption.get("user_id", "")
        user_login = redemption.get("user_login", "")
        user_name = redemption.get("user_name", "")
        user_input = redemption.get("user_input", "")

        # 1. Validate YouTube URL
        video_id = extract_youtube_video_id(user_input)
        if not video_id:
            reason = "Нужна прямая ссылка на YouTube видео (youtube.com или youtu.be)."
            await self._refund_redemption(rid, reward_id, user_name, reason)
            return

        normalized_url = f"https://www.youtube.com/watch?v={video_id}"

        # 2. Capacity & limit checks
        ok, reason = self.queue.can_add(user_id, normalized_url)
        if not ok:
            await self._refund_redemption(rid, reward_id, user_name, reason)
            return

        # 3. Resolve metadata via yt-dlp
        try:
            track = await asyncio.get_event_loop().run_in_executor(
                None, self.player.resolve, normalized_url
            )
        except PlayerError as e:
            await self._refund_redemption(rid, reward_id, user_name, str(e))
            return

        if track is None:
            await self._refund_redemption(
                rid, reward_id, user_name, "Видео недоступно или является трансляцией."
            )
            return

        # 4. Duration limit
        if (
            self.cfg.max_track_seconds > 0
            and track.duration_seconds is not None
            and track.duration_seconds > self.cfg.max_track_seconds
        ):
            max_mins = self.cfg.max_track_seconds // 60
            track_mins = track.duration_seconds // 60
            await self._refund_redemption(
                rid,
                reward_id,
                user_name,
                f"Трек слишком длинный ({track_mins} мин, лимит {max_mins} мин).",
            )
            return

        # 5. Enqueue
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

        chat_msg = f"✅ {user_name} заказал: {track.title} (позиция в очереди #{pos})"
        self._schedule_chat(chat_msg)

        # 6. Trigger playback if idle
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
            logger.info("Заказ %s отменён, баллы возвращены (%s): %s", redemption_id, user_name, reason)
            self._schedule_chat(f"↩ @{user_name}, баллы возвращены: {reason}")
        except TwitchAPIError as e:
            logger.error("Не удалось вернуть баллы за заказ %s: %s", redemption_id, e)

    # ---------------------------------------------------------------- playback loop

    async def _playback_loop(self) -> None:
        """Play queued tracks one by one. Pauses Kopuz while any track plays."""
        if self._playing:
            return

        self._playing = True
        self._playback_task = asyncio.current_task()

        try:
            while self._running:
                item = self.queue.pop_next()
                if item is None:
                    # Queue is empty: wait for debounce delay before unpausing Kopuz
                    if self.pause_state.get():
                        await asyncio.sleep(self.cfg.resume_debounce_seconds)
                        # Check if a new track arrived during the debounce sleep
                        if self.queue.is_empty():
                            self.kopuz.play()
                            self.pause_state.clear()
                            self.queue.save(self.state_dir / "queue.json")
                            self._schedule_chat("▶ Очередь заказов пуста, локальная музыка возобновлена.")
                            break
                        else:
                            continue
                    else:
                        break

                track = Track(
                    url=item.track_url,
                    title=item.track_title,
                    duration_seconds=item.duration_seconds,
                    source="url",
                    requester=item.requester_name,
                    requester_login=item.requester_login,
                )

                # Ensure Kopuz is paused
                if self.cfg.pause_kopuz_during_request and not self.pause_state.get():
                    self.kopuz.pause()
                    self.pause_state.set(True)

                self._schedule_chat(
                    f"▶ Сейчас играет: {track.title} (заказ от {item.requester_name})"
                )

                try:
                    await asyncio.get_event_loop().run_in_executor(
                        None, self.player.play, track
                    )
                except PlayerError as e:
                    logger.error("Ошибка воспроизведения трека '%s': %s", track.title, e)
                    await self._refund_redemption(
                        item.redemption_id,
                        self._reward_id or "",
                        item.requester_name,
                        str(e),
                    )
                    continue

                # Finished playing normally: fulfill redemption on Twitch
                if item.redemption_id and self._reward_id:
                    try:
                        await asyncio.get_event_loop().run_in_executor(
                            None,
                            self.api.update_redemption,
                            self.cfg.broadcaster_user_id,
                            self._reward_id,
                            [item.redemption_id],
                            "FULFILLED",
                        )
                    except Exception as e:
                        logger.warning("Не удалось отметить заказ %s как исполненный: %s", item.redemption_id, e)

                self.queue.save(self.state_dir / "queue.json")
        finally:
            self._playing = False
            self._playback_task = None
            # If shutting down while Kopuz was paused, ensure it's resumed
            if not self._running and self.cfg.pause_kopuz_during_request and self.pause_state.get():
                self.kopuz.play()
                self.pause_state.clear()

    def _schedule_chat(self, message: str) -> None:
        if self.chat:
            self.chat._fire(message)

    # ---------------------------------------------------------------- main life-cycle

    async def _on_eventsub_redemption(self, event: dict) -> None:
        await self.redemption_queue.put(event)

    async def run(self) -> None:
        self._running = True
        await self.startup()

        self.chat = Chat(
            self.api,
            self.cfg.broadcaster_user_id,
            self._bot_user_id or "",
            self.cfg.announce_in_chat,
        )

        # 1. Start redemption processor
        self._processor_task = asyncio.create_task(self._process_redemptions())

        # 2. Start EventSub WebSocket client
        self._eventsub = EventSubClient(
            on_session_welcome=self._on_eventsub_welcome,
            on_redemption=self._on_eventsub_redemption,
        )
        self._eventsub_task = asyncio.create_task(self._eventsub.run())

        # 3. If persisted queue has tracks from earlier, start playing them immediately
        if not self.queue.is_empty() and not self._playing:
            asyncio.create_task(self._playback_loop())

        logger.info("✓ Бот успешно запущен и ожидает заказов.")
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
