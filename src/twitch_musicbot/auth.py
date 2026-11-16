"""Twitch OAuth device-code flow and token storage.

The bot runs on the streamer's PC without a web server, so it uses the
Device Code Grant Flow. The user sees a code and activates it at
twitch.tv/activate. Access tokens live 4 hours; refresh tokens are single-use
and expire after 30 days of inactivity. The bot refreshes automatically and
stores both tokens in tokens.json.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import httpx

TOKEN_URL = "https://id.twitch.tv/oauth2/token"
DEVICE_URL = "https://id.twitch.tv/oauth2/device"
VALIDATE_URL = "https://id.twitch.tv/oauth2/validate"


class AuthError(RuntimeError):
    """Raised when Twitch OAuth cannot be completed."""


class TwitchAuth:
    """Manages device-code authorization and access-token refresh."""

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        scopes: str,
        token_file: str | Path = "tokens.json",
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.scopes = scopes
        self.token_file = Path(token_file)
        self.access_token: str | None = None
        self.refresh_token: str | None = None
        self.expires_at: float = 0.0

    # ------------------------------------------------------------------ load/save

    def load(self) -> bool:
        """Load stored tokens from disk. Returns True if a refresh token exists."""
        if not self.token_file.exists():
            return False
        try:
            data = json.loads(self.token_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return False
        self.access_token = data.get("access_token")
        self.refresh_token = data.get("refresh_token")
        self.expires_at = float(data.get("expires_at", 0))
        return bool(self.refresh_token)

    def save(self) -> None:
        """Persist access + refresh token to disk."""
        data = {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at,
            "scopes": self.scopes,
        }
        self.token_file.parent.mkdir(parents=True, exist_ok=True)
        self.token_file.write_text(json.dumps(data, indent=2), encoding="utf-8")

    # ---------------------------------------------------------------- device flow

    def device_authorize(self) -> dict[str, Any]:
        """Start device code flow. Returns the device payload including user_code."""
        resp = httpx.post(
            DEVICE_URL,
            data={
                "client_id": self.client_id,
                "scopes": self.scopes,
            },
            timeout=30.0,
        )
        resp.raise_for_status()
        return resp.json()

    def poll_for_token(self, device_code: str, interval: int) -> dict[str, Any]:
        """Exchange device code for tokens. Blocks until user authorizes or expires."""
        while True:
            resp = httpx.post(
                TOKEN_URL,
                data={
                    "client_id": self.client_id,
                    "device_code": device_code,
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                },
                timeout=30.0,
            )
            if resp.status_code == 400:
                error = resp.json().get("error", "")
                if error == "authorization_pending":
                    time.sleep(interval)
                    continue
                if error in ("slow_down",):
                    time.sleep(interval + 1)
                    continue
                resp.raise_for_status()
            resp.raise_for_status()
            break
        data = resp.json()
        self.access_token = data["access_token"]
        self.refresh_token = data.get("refresh_token")
        self.expires_at = time.time() + float(data.get("expires_in", 14400))
        self.save()
        return data

    def authorize_interactive(self) -> None:
        """Run the full interactive device-code flow. Prints instructions for the user."""
        payload = self.device_authorize()
        user_code = payload["user_code"]
        uri = payload["verification_uri"]
        interval = int(payload.get("interval", 5))
        expires_in = int(payload.get("expires_in", 1800))

        print("\n" + "=" * 60)
        print("  Twitch авторизация (Device Code Flow)")
        print("=" * 60)
        print(f"  1. Откройте в браузере: {uri}")
        print(f"  2. Введите код: {user_code}")
        print(f"  3. Авторизуйте бота от имени стрим-аккаунта.")
        print(f"  Код действителен {expires_in // 60} минут.")
        print("=" * 60 + "\n")

        self.poll_for_token(payload["device_code"], interval)
        print("✓ Авторизация завершена. Токены сохранены.\n")

    # ------------------------------------------------------------------- refresh

    def is_expired(self) -> bool:
        return time.time() >= self.expires_at - 60  # 1-minute safety margin

    def refresh(self) -> None:
        """Refresh the access token using the stored refresh token."""
        if not self.refresh_token:
            raise AuthError("Нет refresh token. Запустите авторизацию заново.")
        resp = httpx.post(
            TOKEN_URL,
            data={
                "client_id": self.client_id,
                "grant_type": "refresh_token",
                "refresh_token": self.refresh_token,
            },
            timeout=30.0,
        )
        if resp.status_code != 200:
            raise AuthError(f"Не удалось обновить токен: {resp.status_code} {resp.text}")
        data = resp.json()
        self.access_token = data["access_token"]
        # Twitch rotation: new refresh token may be issued
        if "refresh_token" in data:
            self.refresh_token = data["refresh_token"]
        self.expires_at = time.time() + float(data.get("expires_in", 14400))
        self.save()

    def ensure_valid(self) -> None:
        """Ensure we have a valid access token, refreshing if needed."""
        if self.access_token is None or self.is_expired():
            if self.refresh_token:
                try:
                    self.refresh()
                    return
                except AuthError:
                    pass
            raise AuthError(
                "Токен истек и не может быть обновлен. Запустите бота заново для "
                "пройти Device Code Flow."
            )

    def validate(self) -> dict[str, Any] | None:
        """Call /validate to confirm the token is still good. Returns user info or None."""
        try:
            resp = httpx.get(
                VALIDATE_URL,
                headers={"Authorization": f"OAuth {self.access_token}"},
                timeout=15.0,
            )
            if resp.status_code == 200:
                return resp.json()
        except httpx.RequestError:
            pass
        return None

    def auth_headers(self) -> dict[str, str]:
        self.ensure_valid()
        assert self.access_token is not None
        return {"Authorization": f"Bearer {self.access_token}"}
