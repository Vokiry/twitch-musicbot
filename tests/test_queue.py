"""Tests for the request queue with limits, cooldowns, and dedup."""

import tempfile
import time
import unittest
from pathlib import Path

from twitch_musicbot.queue import DEDUP_WINDOW_SECONDS, Queue, QueuedTrack


def _make_cfg(
    max_track_seconds: int = 0,
    max_queue_size: int = 10,
    max_per_user: int = 2,
    user_cooldown_seconds: int = 0,
):
    class C:
        pass

    c = C()
    c.max_track_seconds = max_track_seconds
    c.max_queue_size = max_queue_size
    c.max_per_user = max_per_user
    c.user_cooldown_seconds = user_cooldown_seconds
    return c


class TestQueue(unittest.TestCase):
    def test_add_and_pop(self):
        q = Queue(_make_cfg())
        self.assertTrue(q.is_empty())
        pos = q.add(
            QueuedTrack(
                track_title="Track A",
                track_url="https://youtu.be/11111111111",
                duration_seconds=120,
                requester_id="u1",
                requester_login="user1",
                requester_name="User 1",
            )
        )
        self.assertEqual(pos, 1)
        self.assertEqual(q.count(), 1)
        self.assertFalse(q.is_empty())

        item = q.pop_next()
        self.assertIsNotNone(item)
        self.assertEqual(item.track_title, "Track A")
        self.assertTrue(q.is_empty())

    def test_max_per_user(self):
        q = Queue(_make_cfg(max_per_user=2, user_cooldown_seconds=0))
        q.add(
            QueuedTrack(
                track_title="A",
                track_url="https://youtu.be/11111111111",
                duration_seconds=100,
                requester_id="u1",
                requester_login="u1",
                requester_name="U1",
            )
        )
        q.add(
            QueuedTrack(
                track_title="B",
                track_url="https://youtu.be/22222222222",
                duration_seconds=100,
                requester_id="u1",
                requester_login="u1",
                requester_name="U1",
            )
        )

        # Same user blocked by max_per_user
        ok, reason = q.can_add("u1", "https://youtu.be/33333333333")
        self.assertFalse(ok)
        self.assertIn("лимит", reason)

        # Different user allowed
        ok, reason = q.can_add("u2", "https://youtu.be/33333333333")
        self.assertTrue(ok)

    def test_user_cooldown(self):
        q = Queue(_make_cfg(max_per_user=5, user_cooldown_seconds=60))
        q.add(
            QueuedTrack(
                track_title="A",
                track_url="https://youtu.be/11111111111",
                duration_seconds=100,
                requester_id="u1",
                requester_login="u1",
                requester_name="U1",
            )
        )
        ok, reason = q.can_add("u1", "https://youtu.be/22222222222")
        self.assertFalse(ok)
        self.assertTrue("секунд" in reason or "с." in reason)

    def test_max_queue_size(self):
        q = Queue(_make_cfg(max_queue_size=2, max_per_user=5))
        q.add(
            QueuedTrack(
                track_title="A",
                track_url="https://youtu.be/11111111111",
                duration_seconds=100,
                requester_id="u1",
                requester_login="u1",
                requester_name="U1",
            )
        )
        q.add(
            QueuedTrack(
                track_title="B",
                track_url="https://youtu.be/22222222222",
                duration_seconds=100,
                requester_id="u2",
                requester_login="u2",
                requester_name="U2",
            )
        )
        ok, reason = q.can_add("u3", "https://youtu.be/33333333333")
        self.assertFalse(ok)
        self.assertIn("Очередь заполнена", reason)

    def test_dedup_window_and_expiry(self):
        q = Queue(_make_cfg(max_queue_size=10, max_per_user=5))
        url = "https://youtu.be/11111111111"
        q.add(
            QueuedTrack(
                track_title="A",
                track_url=url,
                duration_seconds=100,
                requester_id="u1",
                requester_login="u1",
                requester_name="U1",
            )
        )
        # Duplicate rejected
        ok, reason = q.can_add("u2", url)
        self.assertFalse(ok)
        self.assertIn("уже был заказан", reason)

        # Simulate expiration
        q._recent_urls[url] = time.time() - (DEDUP_WINDOW_SECONDS + 10)
        ok, reason = q.can_add("u2", url)
        self.assertTrue(ok)

    def test_persistence_roundtrip(self):
        cfg = _make_cfg()
        q1 = Queue(cfg)
        q1.add(
            QueuedTrack(
                track_title="Track Persistent",
                track_url="https://youtu.be/persists123",
                duration_seconds=200,
                requester_id="u1",
                requester_login="u1",
                requester_name="Streamer",
                redemption_id="redem-999",
            )
        )
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            path = f.name
        try:
            q1.save(path)
            q2 = Queue.load(path, cfg)
            self.assertEqual(q2.count(), 1)
            item = q2.pop_next()
            self.assertIsNotNone(item)
            self.assertEqual(item.track_title, "Track Persistent")
            self.assertEqual(item.redemption_id, "redem-999")
        finally:
            Path(path).unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
