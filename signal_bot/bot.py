"""Telegram bot handlers.

Commands: ``/start``, ``/status``, ``/pause``, ``/resume``, ``/handles``.
Allowlist: only ``ALLOWED_USER_ID`` gets through. Everyone else is silently
dropped (no reply, just a log line).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from typing import Any, Awaitable, Callable

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from signal_bot.pipeline.ingest import (
    S_CIRCUIT_OPEN_UNTIL,
    S_LAST_POLL_AT,
    S_LAST_POLL_STATUS,
)
from signal_bot.settings import DB_PATH, Settings
from signal_bot.storage import db as dbmod

log = logging.getLogger(__name__)

HandlerFn = Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[None]]
PostInitFn = Callable[[Application], Awaitable[None]]


def allowlist(allowed_user_id: int) -> Callable[[HandlerFn], HandlerFn]:
    """Decorator factory: only ``allowed_user_id`` gets through. Others silent-drop."""

    def decorator(fn: HandlerFn) -> HandlerFn:
        @wraps(fn)
        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            user = update.effective_user
            if user is None or user.id != allowed_user_id:
                log.info(
                    "rejected non-allowed user id=%s username=%s",
                    getattr(user, "id", None),
                    getattr(user, "username", None),
                )
                return
            await fn(update, context)

        return wrapper

    return decorator


def build_application(
    settings: Settings,
    db_path: Path = DB_PATH,
    post_init: PostInitFn | None = None,
) -> Application:
    """Build the python-telegram-bot Application with all Step-A handlers wired.

    ``post_init`` runs once inside PTB's event loop after the bot is ready —
    use this for any async setup (e.g. ``init_db`` and seeding handles).
    """
    builder = Application.builder().token(settings.telegram_bot_token)
    if post_init is not None:
        builder = builder.post_init(post_init)
    app = builder.build()
    app.bot_data["db_path"] = db_path
    app.bot_data["settings"] = settings

    gate = allowlist(settings.allowed_user_id)
    app.add_handler(CommandHandler("start",   gate(cmd_start)))
    app.add_handler(CommandHandler("status",  gate(cmd_status)))
    app.add_handler(CommandHandler("pause",   gate(cmd_pause)))
    app.add_handler(CommandHandler("resume",  gate(cmd_resume)))
    app.add_handler(CommandHandler("handles", gate(cmd_handles)))
    return app


# ---------- handlers ----------

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    assert msg is not None
    await msg.reply_text(
        "Signal bot online.\n"
        "Commands: /status /pause /resume /handles\n"
        "Build state: Step A (no ingest yet)."
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db_path: Path = context.application.bot_data["db_path"]
    msg = update.effective_message
    assert msg is not None

    async with dbmod.connect(db_path) as conn:
        c = await dbmod.counts(conn)
        paused = await dbmod.is_paused(conn)
        last_poll_raw = await dbmod.get_state(conn, S_LAST_POLL_AT, None)
        last_status = await dbmod.get_state(conn, S_LAST_POLL_STATUS, "")
        circuit_until_raw = await dbmod.get_state(conn, S_CIRCUIT_OPEN_UNTIL, "")
        errs = await dbmod.recent_errors(conn, limit=5)

    state_label = "PAUSED" if paused else "ACTIVE"
    lines = [f"State: {state_label}"]
    lines.append(f"Last poll: {_format_when(last_poll_raw)}{_status_suffix(last_status)}")

    circuit_line = _circuit_line(circuit_until_raw)
    if circuit_line:
        lines.append(circuit_line)

    lines.append(
        f"Tweets: {c['tweets']}  Clusters: {c['clusters']}  Reports: {c['reports']}"
    )
    lines.append(
        f"Handles: {c['handles']}  Errors: {c['errors']}  Feedback: {c['feedback']}"
    )

    if errs:
        lines.append("")
        lines.append("Recent errors:")
        for e in errs:
            handle = e["handle"] or "-"
            short = e["message"][:80]
            when = _format_when(e["timestamp"])
            lines.append(f"  [{when}] {e['error_type']} ({handle}): {short}")
    await msg.reply_text("\n".join(lines))


def _format_when(ts: str | None) -> str:
    if not ts:
        return "never"
    try:
        dt = datetime.fromisoformat(ts)
    except ValueError:
        return ts
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - dt
    secs = int(delta.total_seconds())
    rel = _humanize_seconds(secs)
    return f"{dt.strftime('%Y-%m-%d %H:%M')} UTC ({rel})"


def _humanize_seconds(secs: int) -> str:
    if secs < 0:
        return f"in {_humanize_seconds(-secs)}"
    if secs < 60:
        return f"{secs}s ago"
    if secs < 3600:
        return f"{secs // 60}m ago"
    if secs < 86400:
        return f"{secs // 3600}h ago"
    return f"{secs // 86400}d ago"


def _status_suffix(status: str | None) -> str:
    if not status:
        return ""
    return f"  [{status}]"


def _circuit_line(circuit_until_raw: str | None) -> str | None:
    if not circuit_until_raw:
        return None
    try:
        until = datetime.fromisoformat(circuit_until_raw)
    except ValueError:
        return None
    if until.tzinfo is None:
        until = until.replace(tzinfo=timezone.utc)
    if until <= datetime.now(timezone.utc):
        return None
    remaining = until - datetime.now(timezone.utc)
    return (
        f"Circuit: OPEN until {until.strftime('%Y-%m-%d %H:%M')} UTC "
        f"({_humanize_remaining(int(remaining.total_seconds()))})"
    )


def _humanize_remaining(secs: int) -> str:
    if secs < 60:
        return f"in {secs}s"
    if secs < 3600:
        return f"in {secs // 60}m"
    if secs < 86400:
        return f"in {secs // 3600}h"
    return f"in {secs // 86400}d"


async def cmd_pause(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db_path: Path = context.application.bot_data["db_path"]
    msg = update.effective_message
    assert msg is not None
    async with dbmod.connect(db_path) as conn:
        await dbmod.set_paused(conn, True)
    await msg.reply_text("Polling paused. /resume to re-enable.")


async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db_path: Path = context.application.bot_data["db_path"]
    msg = update.effective_message
    assert msg is not None
    async with dbmod.connect(db_path) as conn:
        await dbmod.set_paused(conn, False)
    await msg.reply_text("Polling resumed.")


async def cmd_handles(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db_path: Path = context.application.bot_data["db_path"]
    msg = update.effective_message
    assert msg is not None
    async with dbmod.connect(db_path) as conn:
        handles = await dbmod.list_handles(conn)
    if not handles:
        await msg.reply_text(
            "No handles configured. (Will seed from config/handles.yaml on next start.)"
        )
        return
    lines = ["Tracked handles:"]
    for h in handles:
        marker = "[H]" if h["priority"] >= 1 else "   "
        suffix = "" if h["enabled"] else " (disabled)"
        lines.append(f" {marker} @{h['handle']}{suffix}")
    await msg.reply_text("\n".join(lines))
