"""Request queue with limits, cooldowns, de-dup and blocklist.

All limits come from Config and can be changed by the streamer in config.toml.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("twitch_musicbot.queue")


@dataclass
class QueuedTrack:
    track_title: str
    track_url: str
    duration_seconds: int | None
    requester_id: str
    requester_login: str
    requester_name: str
    redeemed_at: float = field(default_factory=time.time)
    redemption_id: str = ""
    position: int = 0


class Queue:
    def __init__(self, config) -> None:
        self.cfg = config
        self._items: list[QueuedTrack] = []
        self._user_tracks: dict[str, list[int]] = {}  # user_id -> indices
        self._user_last_redeem: dict[str, float] = {}
        self._recent_urls: dict[str, float] = {}  # url -> timestamp for dedup
        self._blocked_users: set[str] = set()
        self._blocked_terms: set[str] = set()

    # ---------------------------------------------------------------- capacity

    def can_add(self, requester_id: str, url: str) -> tuple[bool, str]:
        """Check whether a new request may be enqueued. Returns (ok, reason)."""
        now = time.time()

        # User cooldown
        if self.cfg.user_cooldown_seconds > 0:
            last = self._user_last_redeem.get(requester_id, 0)
            if now - last < self.cfg.user_cooldown_seconds:
                remaining = int(self.cfg.user_cooldown_seconds - (now - last))
                return False, f"Повторный заказ возможен через {remaining} с."

        # Max per user in queue
        if self.cfg.max_per_user > 0:
            user_count = len(self._user_tracks.get(requester_id, []))
            if user_count >= self.cfg.max_per_user:
                return False, f"У вас уже {user_count} треков в очереди (лимит {self.cfg.max_per_user})."

        # Max queue size
        if self.cfg.max_queue_size > 0 and len(self._items) >= self.cfg.max_queue_size:
            return False, f"Очередь заполнена ({self.cfg.max_queue_size} треков)."

        # Duplicate URL in last N minutes
        if url in self._recent_urls:
            return False, "Этот трек уже был заказан недавно."

        return True, ""

    def add(self, item: QueuedTrack) -> int:
        """Append to queue and return position (1-based)."""
        position = len(self._items) + 1
        item.position = position
        self._items.append(item)

        self._user_tracks.setdefault(item.requester_id, []).append(position - 1)
        self._user_last_redeem[item.requester_id] = item.redeemed_at
        self._recent_urls[item.track_url] = item.redeemed_at
        logger.info(
            "Queue add #%d %s by %s",
            position,
            item.track_title,
            item.requester_name,
        )
        return position

    def pop_next(self) -> QueuedTrack | None:
        """Remove and return the first queued track, shifting positions."""
        if not self._items:
            return None
        item = self._items.pop(0)
        # Re-index user track references
        self._rebuild_user_index()
        for i, it in enumerate(self._items):
            it.position = i + 1
        logger.info("Queue pop: %s (was #%d)", item.track_title, item.position)
        return item

    def peek(self) -> QueuedTrack | None:
        return self._items[0] if self._items else None

    def is_empty(self) -> bool:
        return len(self._items) == 0

    def count(self) -> int:
        return len(self._items)

    def skip_current(self) -> QueuedTrack | None:
        """Alias for pop_next — used when skipping the currently playing request."""
        return self.pop_next()

    def clear_user(self, requester_id: str) -> int:
        """Remove all queued tracks for a user (moderator action). Returns removed count."""
        removed = 0
        remaining = []
        for it in self._items:
            if it.requester_id == requester_id:
                removed += 1
            else:
                remaining.append(it)
        self._items = remaining
        self._rebuild_user_index()
        for i, it in enumerate(self._items):
            it.position = i + 1
        return removed

    # ------------------------------------------------------------- internal

    def _rebuild_user_index(self) -> None:
        self._user_tracks = {}
        for i, it in enumerate(self._items):
            self._user_tracks.setdefault(it.requester_id, []).append(i)

    # ------------------------------------------------------------- persistence

    def save(self, path: str | Path) -> None:
        data = {
            "items": [
                {
                    "track_title": it.track_title,
                    "track_url": it.track_url,
                    "duration_seconds": it.duration_seconds,
                    "requester_id": it.requester_id,
                    "requester_login": it.requester_login,
                    "requester_name": it.requester_name,
                    "redeemed_at": it.redeemed_at,
                    "redemption_id": it.redemption_id,
                    "position": it.position,
                }
                for it in self._items
            ]
        }
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(data, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path, config) -> "Queue":
        q = cls(config)
        p = Path(path)
        if not p.exists():
            return q
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return q
        for raw in data.get("items", []):
            it = QueuedTrack(
                track_title=raw.get("track_title", ""),
                track_url=raw.get("track_url", ""),
                duration_seconds=raw.get("duration_seconds"),
                requester_id=raw.get("requester_id", ""),
                requester_login=raw.get("requester_login", ""),
                requester_name=raw.get("requester_name", ""),
                redeemed_at=raw.get("redeemed_at", time.time()),
                redemption_id=raw.get("redemption_id", ""),
            )
            q.add(it)
        return q
