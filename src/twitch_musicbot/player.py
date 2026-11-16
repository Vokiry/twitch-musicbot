"""YouTube request playback via yt-dlp + headless mpv.

Resolves a YouTube URL (or search query) to a direct audio stream URL with
yt-dlp, then plays it with mpv --no-video --really-quiet. Playback completion
is detected by mpv process exit code. Skip/stop terminates mpv.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
from dataclasses import dataclass

logger = logging.getLogger("twitch_musicbot.player")

YOUTUBE_URL_RE = re.compile(
    r"^(https?://)?(www\.)?(youtube\.com/watch\?v=|youtu\.be/|youtube\.com/embed/|youtube\.com/v/)[A-Za-z0-9_-]{11}"
)

PLAYER_EXISTS_CACHE: dict[str, bool] = {}


@dataclass
class Track:
    url: str
    title: str
    duration_seconds: int | None
    source: str  # "url" or "search"
    requester: str
    requester_login: str


class PlayerError(RuntimeError):
    pass


class Player:
    def __init__(
        self,
        mpv_path: str = "mpv",
        yt_dlp_path: str = "yt-dlp",
        yt_dlp_timeout: int = 20,
        mpv_timeout: int = 0,
    ) -> None:
        self.mpv_path = mpv_path
        self.yt_dlp_path = yt_dlp_path
        self.yt_dlp_timeout = yt_dlp_timeout
        self.mpv_timeout = mpv_timeout
        self._proc: subprocess.Popen | None = None
        self._current: Track | None = None

    # ----------------------------------------------------------------- tools check

    def tool_available(self, name: str) -> bool:
        if name in PLAYER_EXISTS_CACHE:
            return PLAYER_EXISTS_CACHE[name]
        try:
            subprocess.run(
                [name, "--version"],
                capture_output=True,
                timeout=5,
            )
            available = True
        except (FileNotFoundError, subprocess.TimeoutExpired):
            available = False
        PLAYER_EXISTS_CACHE[name] = available
        return available

    # ---------------------------------------------------------------- yt-dlp resolve

    def resolve(self, query: str) -> Track | None:
        """Resolve a YouTube URL or search query to a playable Track.

        Returns None if the video is unavailable, is a livestream, or exceeds
        any externally-imposed limit (duration limits are enforced by the
        queue, not here).
        """
        if YOUTUBE_URL_RE.match(query.strip()):
            url = query.strip()
            if not url.startswith("http"):
                url = "https://" + url
            return self._resolve_url(url, source="url")

        # Not a URL -> treat as a YouTube search query
        search_url = f"ytsearch1:{query}"
        return self._resolve_url(search_url, source="search")

    def _resolve_url(self, url: str, source: str) -> Track | None:
        cmd = [
            self.yt_dlp_path,
            "--no-warnings",
            "--flat-playlist",
            "-J",
            "--no-playlist",
            "--",
            url,
        ]
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.yt_dlp_timeout,
            )
        except FileNotFoundError:
            logger.error("yt-dlp not found at %s", self.yt_dlp_path)
            raise PlayerError("yt-dlp не найден. Проверьте путь в конфиге.")
        except subprocess.TimeoutExpired:
            logger.error("yt-dlp timed out for %s", url)
            raise PlayerError("yt-dlp не уложился в таймаут.")

        if proc.returncode != 0 or not proc.stdout.strip():
            logger.warning("yt-dlp failed for %s: %s", url, proc.stderr.strip()[:200])
            return None

        try:
            info = json.loads(proc.stdout)
        except json.JSONDecodeError:
            logger.warning("yt-dlp returned invalid JSON for %s", url)
            return None

        # yt-dlp -J returns a single video or playlist; with --flat-playlist and
        # --no-playlist a search returns a single-entry playlist or the video.
        entries = info.get("entries")
        if entries:
            info = entries[0]

        if info.get("is_live"):
            logger.info("Skipping livestream: %s", info.get("title", url))
            return None

        duration = info.get("duration")
        title = info.get("title") or "YouTube-трек"
        webpage_url = info.get("webpage_url") or url

        return Track(
            url=webpage_url,
            title=title,
            duration_seconds=int(duration) if duration else None,
            source=source,
            requester="",
            requester_login="",
        )

    # ------------------------------------------------------------------- mpv play

    def play(self, track: Track) -> int:
        """Play a track with mpv. Blocks until mpv exits. Returns mpv exit code."""
        if self._proc is not None:
            self.stop()

        self._current = track
        cmd = [
            self.mpv_path,
            "--no-video",
            "--really-quiet",
            "--force-media-title=" + track.title,
            track.url,
        ]
        logger.info("mpv playing: %s (%s)", track.title, track.url)

        try:
            self._proc = subprocess.Popen(cmd)
        except FileNotFoundError:
            logger.error("mpv not found at %s", self.mpv_path)
            raise PlayerError("mpv не найден. Проверьте путь в конфиге.")

        try:
            if self.mpv_timeout and self.mpv_timeout > 0:
                _, _ = self._proc.communicate(timeout=self.mpv_timeout)
            else:
                _, _ = self._proc.communicate()
        except subprocess.TimeoutExpired:
            self.stop()
            logger.warning("mpv timed out, stopped")
            return -1

        code = self._proc.returncode if self._proc.returncode is not None else -1
        self._proc = None
        self._current = None
        logger.info("mpv finished: %s (code %s)", track.title, code)
        return code

    def stop(self) -> None:
        if self._proc is not None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
            self._proc = None
        self._current = None

    @property
    def is_playing(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    @property
    def current(self) -> Track | None:
        return self._current
