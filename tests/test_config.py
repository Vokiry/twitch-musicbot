"""Tests for config loading defaults."""

from pathlib import Path
import tempfile

from twitch_musicbot.config import Config, DEFAULT_CONFIG


def test_defaults_load_from_empty_file():
    with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as f:
        f.write("")
        path = f.name
    try:
        cfg = Config.load(path)
        assert cfg.reward_title == DEFAULT_CONFIG["reward"]["title"]
        assert cfg.reward_cost == DEFAULT_CONFIG["reward"]["cost"]
        assert cfg.max_track_seconds == DEFAULT_CONFIG["limits"]["max_track_seconds"]
        assert cfg.max_queue_size == DEFAULT_CONFIG["limits"]["max_queue_size"]
        assert cfg.pause_kopuz_during_request is True
    finally:
        Path(path).unlink()


def test_override_values():
    toml = """
[reward]
title = "Моя награда"
cost = 123

[limits]
max_track_seconds = 600
max_queue_size = 5

[behavior]
pause_kopuz_during_request = false
"""
    with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as f:
        f.write(toml)
        path = f.name
    try:
        cfg = Config.load(path)
        assert cfg.reward_title == "Моя награда"
        assert cfg.reward_cost == 123
        assert cfg.max_track_seconds == 600
        assert cfg.max_queue_size == 5
        assert cfg.pause_kopuz_during_request is False
    finally:
        Path(path).unlink()
