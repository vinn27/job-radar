"""Config and .env loading, path resolution, logging setup.

All paths are resolved from this file's location, NOT the current working
directory: Task Scheduler runs tasks without a working directory, so
relative paths based on cwd would silently break.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
LOGS_DIR = ROOT / "logs"
DB_PATH = DATA_DIR / "jobradar.db"
LOG_PATH = LOGS_DIR / "jobradar.log"
CONFIG_PATH = ROOT / "config.json"
ENV_PATH = ROOT / ".env"

REQUIRED_KEYS = ("searches", "profile", "scoring", "digest", "politeness", "email")


def ensure_dirs() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    LOGS_DIR.mkdir(exist_ok=True)


def setup_logging() -> None:
    handler = logging.handlers.RotatingFileHandler(
        LOG_PATH, maxBytes=2 * 1024 * 1024, backupCount=2, encoding="utf-8"
    )
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    handler.setFormatter(fmt)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root.addHandler(console)


def _load_env_file(path: Path) -> dict[str, str]:
    """Minimal .env reader: KEY=VALUE lines, '#' comments, optional quotes.
    Real environment variables (e.g. CI secrets) win over the file."""
    env: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip().strip('"').strip("'")
    for key, value in os.environ.items():
        if key.startswith(("JOB_RADAR_", "ADZUNA_")):
            env[key] = value
    return env


@dataclass
class Settings:
    raw: dict
    env: dict[str, str]

    @property
    def searches(self) -> list[dict]:
        return self.raw["searches"]

    @property
    def profile(self) -> dict:
        return self.raw["profile"]

    @property
    def resumes(self) -> list[dict]:
        return self.raw.get("resumes", [])

    @property
    def scoring(self) -> dict:
        return self.raw["scoring"]

    @property
    def digest(self) -> dict:
        return self.raw["digest"]

    @property
    def politeness(self) -> dict:
        return self.raw["politeness"]

    @property
    def email(self) -> dict:
        return self.raw["email"]

    @property
    def sources(self) -> dict:
        return self.raw.get("sources", {})

    @property
    def smtp_user(self) -> str:
        return self.env.get("JOB_RADAR_SMTP_USER", "")

    @property
    def smtp_password(self) -> str:
        return self.env.get("JOB_RADAR_SMTP_PASSWORD", "")


def load_settings() -> Settings:
    if not CONFIG_PATH.exists():
        raise SystemExit(
            f"config.json not found at {CONFIG_PATH}.\n"
            f"Copy config.example.json to config.json and edit it first."
        )
    raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    missing = [k for k in REQUIRED_KEYS if k not in raw]
    if missing:
        raise SystemExit(f"config.json is missing required keys: {', '.join(missing)}")
    return Settings(raw=raw, env=_load_env_file(ENV_PATH))
