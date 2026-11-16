"""Persistent state: whether Kopuz was paused by this bot.

Written to disk so a restart can recover the fact that Kopuz was paused
for a request. On startup, if the flag is set, the bot checks Kopuz state
and resumes it (fail-open) unless a request is actively playing.
"""

from __future__ import annotations

import json
from pathlib import Path

STATE_FILE_NAME = "paused_by_us.json"


class PauseState:
    def __init__(self, state_dir) -> None:
        self.dir = Path(state_dir)
        self.path = self.dir / STATE_FILE_NAME
        self.dir.mkdir(parents=True, exist_ok=True)

    def set(self, value: bool) -> None:
        self.path.write_text(
            json.dumps({"paused_by_us": value}), encoding="utf-8"
        )

    def get(self) -> bool:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return bool(data.get("paused_by_us", False))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return False

    def clear(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
