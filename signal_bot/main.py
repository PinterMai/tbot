"""Process entry point.

Loads settings, builds the Telegram Application, schedules the ingest cycle,
and starts long-polling. DB init / handle seeding / source construction /
job scheduling all happen inside PTB's event loop via ``post_init``
(this avoids the ``asyncio.run() then run_polling()`` event-loop conflict).
"""
from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

import yaml
from telegram import BotCommand
from telegram.ext import Application, ContextTypes

from signal_bot.bot import build_application
from signal_bot.pipeline.ingest import run_ingest_cycle
from signal_bot.settings import CONFIG_DIR, DB_PATH, REPO_ROOT, Settings, load_settings
from signal_bot.storage import db as dbmod

log = logging.getLogger("signal_bot.main")

LOG_DIR: Path = REPO_ROOT / "logs"
LOG_FILE: Path = LOG_DIR / "signal_bot.log"
LOG_MAX_BYTES: int = 10 * 1024 * 1024
LOG_BACKUP_COUNT: int = 5

BOT_COMMANDS: list[BotCommand] = [
    BotCommand("start",   "Show welcome message"),
    BotCommand("status",  "Show pipeline counts, last poll, recent errors"),
    BotCommand("pause",   "Pause polling"),
    BotCommand("resume",  "Resume polling"),
    BotCommand("handles", "List tracked handles"),
]


def _setup_logging(level: str) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-5s %(name)s :: %(message)s")
    root = logging.getLogger()
    root.setLevel(level)

    # Reset handlers in case of re-import (tests, reloads).
    for h in list(root.handlers):
        root.removeHandler(h)

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root.addHandler(console)

    file_h = RotatingFileHandler(
        LOG_FILE, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT, encoding="utf-8"
    )
    file_h.setFormatter(fmt)
    root.addHandler(file_h)

    # httpx logs full URLs at INFO — that includes the bot token. Silence it.
    # apscheduler / telegram.ext are also chatty at INFO; keep them at WARNING.
    for noisy in ("httpx", "httpcore", "apscheduler", "telegram.ext.Application"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


async def _seed_handles_if_empty(db_path: Path, handles_yaml: Path) -> int:
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


def _try_build_source(settings: Settings):
    """Build a TweetSource. Returns ``None`` if config makes ingest impossible."""
    name = settings.tweet_source.lower()
    if name == "twikit":
        from signal_bot.sources.twikit_source import TwikitSource

        try:
            return TwikitSource(
                settings.x_auth_token, cookies_file=settings.x_cookies_file
            )
        except RuntimeError as exc:
            log.warning("Ingest disabled — %s", exc)
            return None
    log.warning("Unknown TWEET_SOURCE %r — ingest disabled", settings.tweet_source)
    return None


async def _ingest_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    app = context.application
    db_path: Path = app.bot_data["db_path"]
    source = app.bot_data.get("source")
    if source is None:
        return
    settings: Settings = app.bot_data["settings"]

    async def _alert(message: str) -> None:
        await context.bot.send_message(chat_id=settings.allowed_user_id, text=message)

    try:
        await run_ingest_cycle(db_path, source, alert_fn=_alert)
    except Exception:  # noqa: BLE001
        log.exception("Unhandled exception in ingest cycle")


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

    async def _post_init(application: Application) -> None:
        await dbmod.init_db(DB_PATH)
        seeded = await _seed_handles_if_empty(DB_PATH, handles_yaml)
        if seeded:
            log.info("Seeded %d handles from %s", seeded, handles_yaml)

        try:
            await application.bot.set_my_commands(BOT_COMMANDS)
        except Exception:  # noqa: BLE001
            log.exception("set_my_commands failed (non-fatal)")

        source = _try_build_source(settings)
        application.bot_data["source"] = source

        if source is not None:
            interval_seconds = settings.poll_interval_min * 60
            application.job_queue.run_repeating(
                _ingest_job,
                interval=interval_seconds,
                first=10,
                name="ingest_cycle",
            )
            log.info(
                "Ingest scheduled every %ds (first run in 10s)",
                interval_seconds,
            )
        else:
            log.info("Ingest NOT scheduled — source unavailable")

    app = build_application(settings, db_path=DB_PATH, post_init=_post_init)
    log.info("Telegram bot starting (polling). Press Ctrl+C to stop.")
    try:
        app.run_polling(allowed_updates=["message"])
    except KeyboardInterrupt:
        log.info("Shutting down on KeyboardInterrupt.")
        sys.exit(0)


if __name__ == "__main__":
    run()
