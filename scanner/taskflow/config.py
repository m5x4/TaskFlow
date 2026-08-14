"""Environment-backed settings.

Everything the scanner needs comes from `.env` (see `.env.example`). Nothing is
hardcoded and no secret has a default, so a missing key fails loudly at startup
rather than halfway through a scan.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Project root is scanner/ — two levels up from this file (taskflow/config.py).
SCANNER_ROOT = Path(__file__).resolve().parent.parent

load_dotenv(SCANNER_ROOT / ".env")

GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or unusable."""


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc


def _resolve(path_str: str) -> Path:
    """Resolve a possibly-relative path against scanner/, not the shell's cwd.

    The scheduled task runs from an unpredictable working directory, so relative
    paths in .env must anchor somewhere stable.
    """
    path = Path(path_str).expanduser()
    return path if path.is_absolute() else SCANNER_ROOT / path


@dataclass(frozen=True)
class Settings:
    database_url: str
    gemini_api_key: str
    gemini_model: str
    gemini_requests_per_minute: int
    gemini_max_retries: int
    gmail_credentials_path: Path
    gmail_token_path: Path
    gmail_oauth_port: int
    gmail_query: str
    max_emails_per_scan: int
    max_body_chars: int
    initial_lookback_days: int
    target_repo_path: Path | None
    max_index_files: int

    @property
    def has_repo(self) -> bool:
        """Whether file suggestions can be produced at all."""
        return self.target_repo_path is not None and self.target_repo_path.is_dir()


def load_settings(require_db: bool = True, require_api_key: bool = True) -> Settings:
    """Build Settings from the environment.

    `require_*` flags let commands that don't touch a given service still run —
    `taskflow auth`, for example, needs neither the database nor an API key.
    """
    database_url = os.getenv("DATABASE_URL", "").strip()
    if require_db and not database_url:
        raise ConfigError(
            "DATABASE_URL is not set. Copy scanner/.env.example to scanner/.env "
            "and paste your Supabase session-pooler connection string."
        )

    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if require_api_key and not api_key:
        raise ConfigError(
            "GEMINI_API_KEY is not set. Add it to scanner/.env "
            "(create one at aistudio.google.com/apikey)."
        )

    repo_raw = os.getenv("TARGET_REPO_PATH", "").strip()
    target_repo_path = Path(repo_raw).expanduser().resolve() if repo_raw else None

    return Settings(
        database_url=database_url,
        gemini_api_key=api_key,
        gemini_model=os.getenv("GEMINI_MODEL").strip(),
        gemini_requests_per_minute=_int_env("GEMINI_REQUESTS_PER_MINUTE", 15),
        gemini_max_retries=_int_env("GEMINI_MAX_RETRIES", 5),
        gmail_credentials_path=_resolve(
            os.getenv("GMAIL_CREDENTIALS_PATH", "credentials.json")
        ),
        gmail_token_path=_resolve(os.getenv("GMAIL_TOKEN_PATH", "token.json")),
        gmail_oauth_port=_int_env("GMAIL_OAUTH_PORT", 8080),
        gmail_query=os.getenv(
            "GMAIL_QUERY", "-in:spam -in:trash -category:promotions"
        ).strip(),
        max_emails_per_scan=_int_env("MAX_EMAILS_PER_SCAN", 50),
        max_body_chars=_int_env("MAX_BODY_CHARS", 20000),
        initial_lookback_days=_int_env("INITIAL_LOOKBACK_DAYS", 7),
        target_repo_path=target_repo_path,
        max_index_files=_int_env("MAX_INDEX_FILES", 400),
    )
