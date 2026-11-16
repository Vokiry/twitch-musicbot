"""Entry point: python -m twitch_musicbot"""

from __future__ import annotations

import asyncio
import logging
import sys

from .config import Config
from .main import MusicBot

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def setup_logging(log_file: str | None) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if log_file:
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT, handlers=handlers)


def main() -> int:
    import os

    config_path = os.environ.get("TWITCH_MUSICBOT_CONFIG", "config.json")
    config = Config.load(config_path)
    setup_logging(config.log_file or None)

    bot = MusicBot(config)
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        logging.info("Прервано пользователем.")
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
