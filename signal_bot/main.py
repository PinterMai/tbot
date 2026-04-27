"""Process entry point.

Loads settings, builds the Telegram Application, and starts long-polling.
DB init and handle seeding happen inside PTB's event loop via ``post_init``
(this avoids the ``asyncio.run() then run_polling()`` event-loop conflict).

Step A scope: no scheduler, no ingest. Step B+ will add a JobQueue task.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import yaml
from telegram.ext import Application

from signal_bot.bot import build_application
from signal_bot.settings import CONFIG_DIR, DB_PATH, load_settings
from signal_bot.storage import db as dbmod

log = logging.getLogger("signal_bot.main")


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-5s %(name)s :: %(message)s",
    )
    # httpx logs full URLs at INFO — that includes the bot token. Silence it.
    # apscheduler / telegram.ext are also chatty at INFO; keep them at WARNING.
    for noisy in ("httpx", "httpcore", "apscheduler", "telegram.ext.Application"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


async def _seed_handles_if_empty(db_path: Path, handles_yaml: Path) -> int:
    """If the handles table is empty, seed it from ``handles.yaml``. Returns count."""
    async with dbmod.connect(db_path) as conn:
        existing = await dbmod.list_handles(conn)
        if existing:
            return 0
        if not handles_yaml.exists():
            log.warning("No handles in DB and config file missing: %s", handles_yaml)
            return 0
        with handles_yaml.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        normal = list(data.get("handles", []) or [])
        high = set(data.get("high_priority", []) or [])
        all_handles = set(normal) | high
        for h in all_handles:
            await dbmod.upsert_handle(conn, h, priority=1 if h in high else 0)
        return len(all_handles)


def run() -> None:
    settings = load_settings()
    _setup_logging(settings.log_level)
    log.info(
        "starting | source=%s poll_min=%s dry_run=%s",
        settings.tweet_source,
        settings.poll_interval_min,
        settings.dry_run,
    )

    handles_yaml = CONFIG_DIR / "handles.yaml"

    async def _post_init(_app: Application) -> None:
        await dbmod.init_db(DB_PATH)
        seeded = await _seed_handles_if_empty(DB_PATH, handles_yaml)
        if seeded:
            log.info("Seeded %d handles from %s", seeded, handles_yaml)

    app = build_application(settings, db_path=DB_PATH, post_init=_post_init)
    log.info("Telegram bot starting (polling). Press Ctrl+C to stop.")
    try:
        app.run_polling(allowed_updates=["message"])
    except KeyboardInterrupt:
        log.info("Shutting down on KeyboardInterrupt.")
        sys.exit(0)


if __name__ == "__main__":
    run()
