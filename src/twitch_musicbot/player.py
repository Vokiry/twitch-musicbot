"""YouTube request playback via yt-dlp + headless mpv.

Resolves a YouTube URL to a direct audio stream URL with yt-dlp,
then plays it with mpv --no-video --really-quiet. Playback completion
is detected by mpv process exit code. Skip/stop terminates mpv.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("twitch_musicbot.player")

PLAYER_EXISTS_CACHE: dict[str, bool] = {}


def extract_youtube_video_id(url_or_str: str) -> str | None:
    """Extract and validate an 11-character YouTube video ID from various URL formats.

    Supports:
      - https://www.youtube.com/watch?v=dQw4w9WgXcQ
      - https://youtube.com/watch?feature=shared&v=dQw4w9WgXcQ
      - https://m.youtube.com/watch?v=dQw4w9WgXcQ
      - https://music.youtube.com/watch?v=dQw4w9WgXcQ
      - https://youtu.be/dQw4w9WgXcQ?si=abcdef
      - https://www.youtube.com/shorts/dQw4w9WgXcQ
      - https://www.youtube.com/embed/dQw4w9WgXcQ
      - https://www.youtube.com/v/dQw4w9WgXcQ
    """
    s = url_or_str.strip()
    if not s:
        return None
    if not s.startswith(("http://", "https://")):
        s = "https://" + s

    try:
        parsed = urllib.parse.urlparse(s)
    except Exception:
        return None

    hostname = (parsed.hostname or "").lower()
    valid_hosts = {
        "youtube.com",
        "www.youtube.com",
        "m.youtube.com",
        "music.youtube.com",
        "youtu.be",
    }
    if hostname not in valid_hosts and not hostname.endswith(".youtube.com"):
        return None

    path = parsed.path

    # youtu.be/<id>
    if "youtu.be" in hostname:
        vid = path.strip("/").split("/")[0]
        if len(vid) == 11:
            return vid
        return None

    # youtube.com/watch?v=<id>
    if path in ("/watch", "/watch/"):
        qs = urllib.parse.parse_qs(parsed.query)
        v_list = qs.get("v")
        if v_list and len(v_list[0]) == 11:
            return v_list[0]
        return None

    # youtube.com/shorts/<id>, /embed/<id>, /v/<id>
    for prefix in ("/shorts/", "/embed/", "/v/"):
        if path.startswith(prefix):
            parts = path[len(prefix) :].split("/")
            if parts and len(parts[0]) == 11:
                return parts[0]

    return None


@dataclass
class Track:
    url: str
    title: str
    duration_seconds: int | None
    source: str
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

    def tool_available(self, path_or_name: str) -> bool:
        if path_or_name in PLAYER_EXISTS_CACHE:
            return PLAYER_EXISTS_CACHE[path_or_name]
        try:
            kwargs = {}
            if sys.platform == "win32":
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            subprocess.run(
                [path_or_name, "--version"],
                capture_output=True,
                timeout=5,
                **kwargs,
            )
            available = True
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            available = False
        PLAYER_EXISTS_CACHE[path_or_name] = available
        return available

    def resolve(self, query: str) -> Track | None:
        """Validate input URL, extract video ID, normalize URL and query yt-dlp."""
        video_id = extract_youtube_video_id(query)
        if not video_id:
            return None

        # Normalize to canonical watch URL (strips playlists and tracking parameters)
        canonical_url = f"https://www.youtube.com/watch?v={video_id}"
        return self._resolve_url(canonical_url)

    def _resolve_url(self, url: str) -> Track | None:
        cmd = [
            self.yt_dlp_path,
            "--no-warnings",
            "--flat-playlist",
            "-J",
            "--no-playlist",
            "--",
            url,
        ]
        kwargs = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.yt_dlp_timeout,
                **kwargs,
            )
        except FileNotFoundError:
            logger.error("yt-dlp не найден по пути %s", self.yt_dlp_path)
            raise PlayerError(f"yt-dlp не найден: {self.yt_dlp_path}")
        except subprocess.TimeoutExpired:
            logger.error("yt-dlp превысил таймаут для %s", url)
            raise PlayerError("Таймаут получения информации о треке")

        if proc.returncode != 0 or not proc.stdout.strip():
            logger.warning("yt-dlp вернул ошибку для %s: %s", url, proc.stderr.strip()[:200])
            return None

        try:
            info = json.loads(proc.stdout)
        except json.JSONDecodeError:
            logger.warning("yt-dlp вернул невалидный JSON для %s", url)
            return None

        entries = info.get("entries")
        if entries:
            info = entries[0]

        if info.get("is_live"):
            logger.info("Пропуск прямой трансляции: %s", info.get("title", url))
            return None

        duration = info.get("duration")
        title = info.get("title") or "YouTube-трек"
        webpage_url = info.get("webpage_url") or url

        return Track(
            url=webpage_url,
            title=title,
            duration_seconds=int(duration) if duration else None,
            source="url",
            requester="",
            requester_login="",
        )

    def play(self, track: Track) -> int:
        """Play a track with headless mpv. Blocks until mpv exits. Returns exit code."""
        if self._proc is not None:
            self.stop()

        self._current = track
        cmd = [
            self.mpv_path,
            "--no-video",
            "--really-quiet",
            f"--script-opts=ytdl_hook-ytdl_path={self.yt_dlp_path}",
            "--force-media-title=" + track.title,
            track.url,
        ]
        logger.info("mpv запуск воспроизведения: %s (%s)", track.title, track.url)

        kwargs = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

        try:
            self._proc = subprocess.Popen(cmd, **kwargs)
        except FileNotFoundError:
            logger.error("mpv не найден по пути %s", self.mpv_path)
            raise PlayerError(f"mpv не найден: {self.mpv_path}")

        try:
            if self.mpv_timeout and self.mpv_timeout > 0:
                _, _ = self._proc.communicate(timeout=self.mpv_timeout)
            else:
                _, _ = self._proc.communicate()
        except subprocess.TimeoutExpired:
            self.stop()
            logger.warning("mpv превысил лимит времени и был остановлен")
            return -1

        code = self._proc.returncode if self._proc.returncode is not None else -1
        self._proc = None
        self._current = None
        logger.info("mpv завершил воспроизведение: %s (код %s)", track.title, code)
        return code

    def stop(self) -> None:
        if self._proc is not None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
            except OSError:
                pass
            self._proc = None
        self._current = None

    @property
    def is_playing(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    @property
    def current(self) -> Track | None:
        return self._current
