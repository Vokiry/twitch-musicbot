"""Configuration loading and saving for twitch-musicbot.

Configuration is stored in a JSON file. All limits, paths, reward settings and
behavior flags are user-editable. The config is validated on load and missing
keys are filled with sensible defaults so the bot can start with a minimal
hand-edited config.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_CONFIG: dict[str, Any] = {
    "twitch": {
        "client_id": "",
        "client_secret": "",
        "broadcaster_user_id": "",
        "scopes": "channel:read:redemptions channel:manage:redemptions user:write:chat",
        "token_file": "tokens.json",
    },
    "paths": {
        "mpv": "mpv",
        "yt_dlp": "yt-dlp",
        "kopuzctl": "kopuzctl",
        "state_dir": "./state",
        "log_file": "",
    },
    "reward": {
        "title": "Заказ музыки",
        "cost": 500,
        "background_color": "#9147FF",
        "is_user_input_required": True,
        "prompt": "Ссылка на YouTube-трек",
        "max_per_stream": 0,
        "max_per_user_per_stream": 0,
        "global_cooldown_seconds": 0,
        "should_redemptions_skip_request_queue": False,
    },
    "limits": {
        "max_track_seconds": 360,
        "max_queue_size": 20,
        "max_per_user": 2,
        "user_cooldown_seconds": 60,
        "resume_debounce_seconds": 2.0,
        "yt_dlp_timeout_seconds": 20,
        "mpv_timeout_seconds": 0,
    },
    "behavior": {
        "pause_kopuz_during_request": True,
        "announce_in_chat": True,
        "auto_refund_on_error": True,
        "youtube_links_only": True,
    },
}


@dataclass
class Config:
    """User-editable configuration with defaults."""

    # Twitch
    client_id: str = ""
    client_secret: str = ""
    broadcaster_user_id: str = ""
    scopes: str = "channel:read:redemptions channel:manage:redemptions user:write:chat"
    token_file: str = "tokens.json"

    # Paths
    mpv: str = "mpv"
    yt_dlp: str = "yt-dlp"
    kopuzctl: str = "kopuzctl"
    state_dir: str = "./state"
    log_file: str = ""

    # Reward
    reward_title: str = "Заказ музыки"
    reward_cost: int = 500
    reward_background_color: str = "#9147FF"
    reward_is_user_input_required: bool = True
    reward_prompt: str = "Ссылка на YouTube-трек"
    reward_max_per_stream: int = 0
    reward_max_per_user_per_stream: int = 0
    reward_global_cooldown_seconds: int = 0
    reward_should_redemptions_skip_request_queue: bool = False

    # Limits
    max_track_seconds: int = 360
    max_queue_size: int = 20
    max_per_user: int = 2
    user_cooldown_seconds: int = 60
    resume_debounce_seconds: float = 2.0
    yt_dlp_timeout_seconds: int = 20
    mpv_timeout_seconds: int = 0

    # Behavior
    pause_kopuz_during_request: bool = True
    announce_in_chat: bool = True
    auto_refund_on_error: bool = True
    youtube_links_only: bool = True

    @classmethod
    def load(cls, path: str | Path) -> "Config":
        """Load config from JSON path, merging missing keys with defaults."""
        path = Path(path)
        data: dict[str, Any] = {}
        if path.exists():
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
        merged = _deep_merge(DEFAULT_CONFIG, data)
        return cls.from_dict(merged)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Config":
        twitch = data.get("twitch", {})
        paths = data.get("paths", {})
        reward = data.get("reward", {})
        limits = data.get("limits", {})
        behavior = data.get("behavior", {})
        return cls(
            client_id=str(twitch.get("client_id", "")),
            client_secret=str(twitch.get("client_secret", "")),
            broadcaster_user_id=str(twitch.get("broadcaster_user_id", "")),
            scopes=str(twitch.get("scopes", cls.scopes)),
            token_file=str(twitch.get("token_file", "tokens.json")),
            mpv=str(paths.get("mpv", "mpv")),
            yt_dlp=str(paths.get("yt_dlp", "yt-dlp")),
            kopuzctl=str(paths.get("kopuzctl", "kopuzctl")),
            state_dir=str(paths.get("state_dir", "./state")),
            log_file=str(paths.get("log_file", "")),
            reward_title=str(reward.get("title", "Заказ музыки")),
            reward_cost=int(reward.get("cost", 500)),
            reward_background_color=str(reward.get("background_color", "#9147FF")),
            reward_is_user_input_required=bool(
                reward.get("is_user_input_required", True)
            ),
            reward_prompt=str(reward.get("prompt", "Ссылка на YouTube-трек")),
            reward_max_per_stream=int(reward.get("max_per_stream", 0)),
            reward_max_per_user_per_stream=int(
                reward.get("max_per_user_per_stream", 0)
            ),
            reward_global_cooldown_seconds=int(
                reward.get("global_cooldown_seconds", 0)
            ),
            reward_should_redemptions_skip_request_queue=bool(
                reward.get("should_redemptions_skip_request_queue", False)
            ),
            max_track_seconds=int(limits.get("max_track_seconds", 360)),
            max_queue_size=int(limits.get("max_queue_size", 20)),
            max_per_user=int(limits.get("max_per_user", 2)),
            user_cooldown_seconds=int(limits.get("user_cooldown_seconds", 60)),
            resume_debounce_seconds=float(
                limits.get("resume_debounce_seconds", 2.0)
            ),
            yt_dlp_timeout_seconds=int(limits.get("yt_dlp_timeout_seconds", 20)),
            mpv_timeout_seconds=int(limits.get("mpv_timeout_seconds", 0)),
            pause_kopuz_during_request=bool(
                behavior.get("pause_kopuz_during_request", True)
            ),
            announce_in_chat=bool(behavior.get("announce_in_chat", True)),
            auto_refund_on_error=bool(behavior.get("auto_refund_on_error", True)),
            youtube_links_only=bool(behavior.get("youtube_links_only", True)),
        )

    def save(self, path: str | Path) -> None:
        """Persist current config back to JSON."""
        path = Path(path)
        data = {
            "twitch": {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "broadcaster_user_id": self.broadcaster_user_id,
                "scopes": self.scopes,
                "token_file": self.token_file,
            },
            "paths": {
                "mpv": self.mpv,
                "yt_dlp": self.yt_dlp,
                "kopuzctl": self.kopuzctl,
                "state_dir": self.state_dir,
                "log_file": self.log_file,
            },
            "reward": {
                "title": self.reward_title,
                "cost": self.reward_cost,
                "background_color": self.reward_background_color,
                "is_user_input_required": self.reward_is_user_input_required,
                "prompt": self.reward_prompt,
                "max_per_stream": self.reward_max_per_stream,
                "max_per_user_per_stream": self.reward_max_per_user_per_stream,
                "global_cooldown_seconds": self.reward_global_cooldown_seconds,
                "should_redemptions_skip_request_queue": self.reward_should_redemptions_skip_request_queue,
            },
            "limits": {
                "max_track_seconds": self.max_track_seconds,
                "max_queue_size": self.max_queue_size,
                "max_per_user": self.max_per_user,
                "user_cooldown_seconds": self.user_cooldown_seconds,
                "resume_debounce_seconds": self.resume_debounce_seconds,
                "yt_dlp_timeout_seconds": self.yt_dlp_timeout_seconds,
                "mpv_timeout_seconds": self.mpv_timeout_seconds,
            },
            "behavior": {
                "pause_kopuz_during_request": self.pause_kopuz_during_request,
                "announce_in_chat": self.announce_in_chat,
                "auto_refund_on_error": self.auto_refund_on_error,
                "youtube_links_only": self.youtube_links_only,
            },
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)


def _deep_merge(default: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = dict(default)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def resolve_state_dir(config: Config) -> Path:
    """Return the absolute state directory, creating it if needed."""
    p = Path(config.state_dir)
    if not p.is_absolute():
        p = Path.cwd() / p
    p.mkdir(parents=True, exist_ok=True)
    return p
