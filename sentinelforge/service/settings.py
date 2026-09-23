"""Runtime configuration, read once from the environment. Nothing here is a machine-specific path."""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass
class Settings:
    state_dir: Path = field(default_factory=lambda: Path(os.environ.get("SF_STATE_DIR", REPO_ROOT / "var")))
    engine: str = field(default_factory=lambda: os.environ.get("SF_ENGINE", "auto"))          # auto | spark | reference
    job_workers: int = field(default_factory=lambda: _int("SF_JOB_WORKERS", 2))
    max_report_bytes: int = field(default_factory=lambda: _int("SF_MAX_REPORT_BYTES", 100_000))
    max_upload_bytes: int = field(default_factory=lambda: _int("SF_MAX_UPLOAD_BYTES", 20_000_000))
    max_alerts_shown: int = field(default_factory=lambda: _int("SF_MAX_ALERTS_SHOWN", 5000))
    run_timeout_s: int = field(default_factory=lambda: _int("SF_RUN_TIMEOUT", 900))
    spark_heap: str = field(default_factory=lambda: os.environ.get("SF_SPARK_HEAP", "2g"))
    # "name:token,name2:token2". When empty and auth is required, a random token is generated and printed once.
    api_tokens: str = field(default_factory=lambda: os.environ.get("SF_API_TOKENS", ""))
    require_auth: bool | None = field(default_factory=lambda: (
        None if "SF_REQUIRE_AUTH" not in os.environ else os.environ["SF_REQUIRE_AUTH"].lower() in ("1", "true", "yes")))
    cors_origins: list = field(default_factory=lambda: [o for o in os.environ.get("SF_CORS_ORIGINS", "").split(",") if o])
    rate_limit_per_minute: int = field(default_factory=lambda: _int("SF_RATE_LIMIT", 120))

    @property
    def db_path(self) -> Path:
        return self.state_dir / "sentinel.db"

    @property
    def runs_dir(self) -> Path:
        return self.state_dir / "runs"

    @property
    def datasets_dir(self) -> Path:
        return self.state_dir / "datasets"

    def token_map(self) -> dict[str, str]:
        """token -> analyst name."""
        out = {}
        for part in filter(None, (p.strip() for p in self.api_tokens.split(","))):
            name, _, token = part.partition(":")
            if token:
                out[token] = name
        return out

    def ensure_dirs(self) -> None:
        for d in (self.state_dir, self.runs_dir, self.datasets_dir):
            d.mkdir(parents=True, exist_ok=True)


def new_token() -> str:
    return secrets.token_urlsafe(24)
