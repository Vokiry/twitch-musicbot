"""Wrapper around kopuzctl for controlling Kopuz playback.

Uses subprocess calls to the kopuzctl CLI (ships with Kopuz). On Windows
Kopuz exposes its daemon API via a named pipe, so kopuzctl works while the
Kopuz desktop app is open — no separate kopuzd needed.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys

logger = logging.getLogger("twitch_musicbot.kopuz")


class KopuzError(RuntimeError):
    pass


class Kopuz:
    def __init__(self, kopuzctl_path: str) -> None:
        self.kopuzctl = kopuzctl_path

    def _run(self, *args: str, timeout: int = 10) -> subprocess.CompletedProcess | None:
        kwargs = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        try:
            return subprocess.run(
                [self.kopuzctl, *args],
                capture_output=True,
                text=True,
                timeout=timeout,
                **kwargs,
            )
        except FileNotFoundError:
            logger.warning("kopuzctl не найден по пути '%s'", self.kopuzctl)
            return None
        except Exception as e:  # noqa: BLE001
            logger.warning("Ошибка при запуске kopuzctl %s: %s", " ".join(args), e)
            return None

    def status(self) -> dict | None:
        """Return parsed `kopuzctl status --json` or None on failure."""
        proc = self._run("status", "--json")
        if proc is None or proc.returncode != 0:
            return None
        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError:
            logger.warning("kopuzctl status вернул невалидный JSON")
            return None

    def is_playing(self) -> bool:
        state = self.status()
        if state is None:
            return False
        phase = state.get("phase") if isinstance(state, dict) else None
        return phase not in (None, "stopped", "paused")

    def pause(self) -> bool:
        proc = self._run("pause")
        if proc is None or proc.returncode != 0:
            err = proc.stderr.strip() if proc else "файл не найден"
            logger.warning("Не удалось поставить Kopuz на паузу: %s", err)
            return False
        logger.info("Kopuz поставлен на паузу")
        return True

    def play(self) -> bool:
        proc = self._run("play")
        if proc is None or proc.returncode != 0:
            err = proc.stderr.strip() if proc else "файл не найден"
            logger.warning("Не удалось снять Kopuz с паузы: %s", err)
            return False
        logger.info("Kopuz снят с паузы")
        return True

    def toggle(self) -> bool:
        proc = self._run("toggle")
        return proc is not None and proc.returncode == 0
