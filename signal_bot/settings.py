"""Environment configuration loading and validation.

Required vars are checked at startup. If anything required is missing,
the process prints a clear list of every missing var and exits with code 1.

Step A only requires `TELEGRAM_BOT_TOKEN` and `ALLOWED_USER_ID`.
`ANTHROPIC_API_KEY` and `X_AUTH_TOKEN` become required when their respective
features actually run, and are checked in their own modules at first use.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT: Path = Path(__file__).resolve().parent.parent
DATA_DIR: Path = REPO_ROOT / "data"
DB_PATH: Path = DATA_DIR / "signal_bot.db"
CONFIG_DIR: Path = REPO_ROOT / "config"

_REQUIRED_FOR_STEP_A: tuple[str, ...] = ("TELEGRAM_BOT_TOKEN", "ALLOWED_USER_ID")


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    allowed_user_id: int
    anthropic_api_key: str | None
    x_auth_token: str | None
    dry_run: bool
    poll_interval_min: int
    tweet_source: str
    log_level: str


def _truthy(s: str | None) -> bool:
    return (s or "").strip().lower() in ("1", "true", "yes", "on")


def load_settings(env_path: Path | None = None) -> Settings:
    """Load and validate settings. Exits the process if required vars are missing."""
    if env_path is None:
        env_path = REPO_ROOT / ".env"
    if env_path.exists():
        load_dotenv(env_path, override=False)

    missing = [k for k in _REQUIRED_FOR_STEP_A if not (os.environ.get(k) or "").strip()]
    if missing:
        print("ERROR: missing required environment variables:", file=sys.stderr)
        for k in missing:
            print(f"  - {k}", file=sys.stderr)
        print(
            f"\nCopy .env.example to .env (at {REPO_ROOT}) and fill these values, "
            "then re-run.",
            file=sys.stderr,
        )
        sys.exit(1)

    raw_uid = os.environ["ALLOWED_USER_ID"].strip()
    try:
        allowed_user_id = int(raw_uid)
    except ValueError:
        print(
            f"ERROR: ALLOWED_USER_ID must be an integer Telegram user ID "
            f"(got {raw_uid!r}).",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        poll_interval_min = int(os.environ.get("POLL_INTERVAL_MIN", "60"))
    except ValueError:
        print("ERROR: POLL_INTERVAL_MIN must be an integer (minutes).", file=sys.stderr)
        sys.exit(1)

    return Settings(
        telegram_bot_token=os.environ["TELEGRAM_BOT_TOKEN"].strip(),
        allowed_user_id=allowed_user_id,
        anthropic_api_key=(os.environ.get("ANTHROPIC_API_KEY") or "").strip() or None,
        x_auth_token=(os.environ.get("X_AUTH_TOKEN") or "").strip() or None,
        dry_run=_truthy(os.environ.get("DRY_RUN", "true")),
        poll_interval_min=poll_interval_min,
        tweet_source=(os.environ.get("TWEET_SOURCE", "twikit") or "twikit").strip(),
        log_level=(os.environ.get("LOG_LEVEL", "INFO") or "INFO").strip().upper(),
    )
