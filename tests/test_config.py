"""Tests for configuration loading and defaults."""

import json
import tempfile
import unittest
from pathlib import Path

from twitch_musicbot.config import Config, DEFAULT_CONFIG


class TestConfig(unittest.TestCase):
    def test_defaults_load_from_empty_or_missing_file(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            f.write("{}")
            path = f.name
        try:
            cfg = Config.load(path)
            self.assertEqual(cfg.reward_title, DEFAULT_CONFIG["reward"]["title"])
            self.assertEqual(cfg.reward_cost, DEFAULT_CONFIG["reward"]["cost"])
            self.assertEqual(cfg.max_track_seconds, DEFAULT_CONFIG["limits"]["max_track_seconds"])
            self.assertEqual(cfg.max_queue_size, DEFAULT_CONFIG["limits"]["max_queue_size"])
            self.assertTrue(cfg.pause_kopuz_during_request)
        finally:
            Path(path).unlink(missing_ok=True)

    def test_defaults_load_from_blank_file(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            f.write("")
            path = f.name
        try:
            cfg = Config.load(path)
            self.assertEqual(cfg.reward_cost, 500)
            self.assertEqual(cfg.max_queue_size, 20)
        finally:
            Path(path).unlink(missing_ok=True)

    def test_override_values(self):
        data = {
            "reward": {
                "title": "Мой трек",
                "cost": 150,
            },
            "limits": {
                "max_track_seconds": 600,
                "max_queue_size": 5,
            },
            "behavior": {
                "pause_kopuz_during_request": False,
            },
        }
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(data, f)
            path = f.name
        try:
            cfg = Config.load(path)
            self.assertEqual(cfg.reward_title, "Мой трек")
            self.assertEqual(cfg.reward_cost, 150)
            self.assertEqual(cfg.max_track_seconds, 600)
            self.assertEqual(cfg.max_queue_size, 5)
            self.assertFalse(cfg.pause_kopuz_during_request)
        finally:
            Path(path).unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
