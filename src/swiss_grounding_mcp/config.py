"""Runtime settings, all overridable through environment variables (prefix SGM_)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
DATA_DIR = PACKAGE_DIR / "data"

try:
    VERSION = version("swiss-grounding-mcp")  # single source of truth: pyproject.toml
except PackageNotFoundError:  # running from a source tree that was never installed
    VERSION = "0.0.0"
REPO_URL = "https://github.com/Gastaan/swiss-grounding-mcp"


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _list(name: str) -> list[str]:
    return [v.strip() for v in os.getenv(name, "").split(",") if v.strip()]


@dataclass
class Settings:
    # Respect robots.txt of every host we fetch from. Required by the challenge to be configurable.
    respect_robots: bool = field(default_factory=lambda: _bool("SGM_RESPECT_ROBOTS", True))
    # Respect the terms of use recorded in data/source_terms.json: hosts whose terms do not allow
    # automated access are never fetched. Required by the challenge to be configurable.
    respect_terms: bool = field(default_factory=lambda: _bool("SGM_RESPECT_TERMS", True))
    # Serve cached responses only; never hit the network (offline demos and tests).
    offline: bool = field(default_factory=lambda: _bool("SGM_OFFLINE", False))
    cache_dir: Path = field(
        default_factory=lambda: Path(
            os.getenv("SGM_CACHE_DIR", Path.home() / ".cache" / "swiss-grounding-mcp")
        )
    )
    # Cache entries untouched for this long are deleted at startup; the cache is also capped in size.
    cache_max_age_s: float = field(default_factory=lambda: float(os.getenv("SGM_CACHE_MAX_DAYS", "30")) * 86400)
    cache_max_mb: float = field(default_factory=lambda: float(os.getenv("SGM_CACHE_MAX_MB", "500")))
    # When a source is down, serve an expired cached copy up to this old (0 disables), labelled as stale.
    max_stale_s: float = field(default_factory=lambda: float(os.getenv("SGM_MAX_STALE_HOURS", "168")) * 3600)
    timeout_s: float = field(default_factory=lambda: float(os.getenv("SGM_HTTP_TIMEOUT", "15")))
    # Upper bound for one whole tool call, however many upstream requests it makes.
    tool_timeout_s: float = field(default_factory=lambda: float(os.getenv("SGM_TOOL_TIMEOUT", "30")))
    # Minimum seconds between two requests to the same host (source etiquette).
    min_interval_s: float = field(default_factory=lambda: float(os.getenv("SGM_MIN_INTERVAL", "0.5")))
    # Standard crawler form: sites that serve an empty shell to unknown agents accept "Mozilla/5.0
    # (compatible; …)", and the string names the project and links to it instead of posing as a browser.
    user_agent: str = field(
        default_factory=lambda: os.getenv(
            "SGM_USER_AGENT", f"Mozilla/5.0 (compatible; SwissGroundingMCP/{VERSION}; +{REPO_URL})"
        )
    )
    robots_token: str = "SwissGroundingMCP"
    # Search: "auto" merges vector results into keyword search when the `semantic` extra and
    # data/embeddings.npz are present; "off" keeps keyword search only.
    semantic: str = field(default_factory=lambda: os.getenv("SGM_SEMANTIC", "auto").strip().lower())
    model_dir: Path = field(
        default_factory=lambda: Path(
            os.getenv("SGM_MODEL_DIR", Path.home() / ".cache" / "swiss-grounding-mcp" / "models")
        )
    )
    # HTTP transport only. Browser origins allowed to call /mcp (clients that send no Origin are unaffected).
    allowed_origins: list[str] = field(default_factory=lambda: _list("SGM_ALLOWED_ORIGINS"))
    # HTTP transport only. When set, /mcp requires "Authorization: Bearer <token>".
    auth_token: str = field(default_factory=lambda: os.getenv("SGM_AUTH_TOKEN", ""))
    # HTTP transport only. Requests per minute per client IP (0 disables).
    rate_limit: int = field(default_factory=lambda: int(os.getenv("SGM_RATE_LIMIT", "600")))


settings = Settings()
