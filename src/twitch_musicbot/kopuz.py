"""Wrapper around kopuzctl for controlling Kopuz playback.

Uses subprocess calls to the kopuzctl CLI (ships with Kopuz). On Windows
Kopuz exposes its daemon API via a named pipe, so kopuzctl works while the
Kopuz desktop app is open — no separate kopuzd needed.
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

logger = logging.getLogger("twitch_musicbot.kopuz")


class KopuzError(RuntimeError):
    pass


class Kopuz:
    def __init__(self, kopuzctl_path: str) -> None:
        self.kopuzctl = kopuzctl_path

    def _run(self, *args: str, timeout: int = 10) -> subprocess.CompletedProcess:
        return subprocess.run(
            [self.kopuzctl, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    def status(self) -> dict | None:
        """Return parsed `kopuzctl status --json` or None on failure."""
        proc = self._run("status", "--json")
        if proc.returncode != 0:
            logger.debug("kopuzctl status failed: %s", proc.stderr.strip())
            return None
        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError:
            logger.warning("kopuzctl status returned invalid JSON")
            return None

    def is_playing(self) -> bool:
        state = self.status()
        if state is None:
            return False
        # Kopuz player_state has a phase field; treat anything not stopped as playing
        phase = state.get("phase") if isinstance(state, dict) else None
        return phase not in (None, "stopped", "paused")

    def pause(self) -> bool:
        proc = self._run("pause")
        if proc.returncode != 0:
            logger.error("kopuzctl pause failed: %s", proc.stderr.strip())
            return False
        logger.info("Kopuz paused by bot")
        return True

    def play(self) -> bool:
        proc = self._run("play")
        if proc.returncode != 0:
            logger.error("kopuzctl play failed: %s", proc.stderr.strip())
            return False
        logger.info("Kopuz resumed by bot")
        return True

    def toggle(self) -> bool:
        proc = self._run("toggle")
        return proc.returncode == 0
