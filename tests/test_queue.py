"""Tests for the request queue with limits."""

import time

from twitch_musicbot.queue import Queue, QueuedTrack


def _make_cfg():
    class C:
        max_track_seconds = 0
        max_queue_size = 10
        max_per_user = 2
        user_cooldown_seconds = 30
        youtube_links_only = True
    return C()


def test_add_and_pop():
    q = Queue(_make_cfg())
    assert q.is_empty()
    pos = q.add(QueuedTrack(
        track_title="A", track_url="https://youtu.be/abc", duration_seconds=100,
        requester_id="u1", requester_login="u1", requester_name="User1",
    ))
    assert pos == 1
    assert q.count() == 1
    item = q.pop_next()
    assert item is not None
    assert item.track_title == "A"
    assert q.is_empty()


def test_max_per_user():
    q = Queue(_make_cfg())
    q.add(QueuedTrack(track_title="A", track_url="https://youtu.be/aaa", duration_seconds=100,
                      requester_id="u1", requester_login="u1", requester_name="U1"))
    q.add(QueuedTrack(track_title="B", track_url="https://youtu.be/bbb", duration_seconds=100,
                      requester_id="u1", requester_login="u1", requester_name="U1"))
    ok, reason = q.can_add("u1", "https://youtu.be/ccc")
    assert ok is False
    assert "лимит" in reason

    ok, reason = q.can_add("u2", "https://youtu.be/ccc")
    assert ok is True


def test_user_cooldown():
    q = Queue(_make_cfg())
    q.add(QueuedTrack(track_title="A", track_url="https://youtu.be/aaa", duration_seconds=100,
                      requester_id="u1", requester_login="u1", requester_name="U1"))
    ok, reason = q.can_add("u1", "https://youtu.be/bbb")
    assert ok is False
    assert "секунд" in reason or "с." in reason
