"""Runtime settings, all overridable through environment variables (prefix SGM_)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
DATA_DIR = PACKAGE_DIR / "data"

VERSION = "0.1.0"
REPO_URL = "https://github.com/Gastaan/swiss-grounding-mcp"


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    # Respect robots.txt of every host we fetch from. Required by the challenge to be configurable.
    respect_robots: bool = field(default_factory=lambda: _bool("SGM_RESPECT_ROBOTS", True))
    # Skip sources whose terms of use do not allow automated access (see sources registry).
    respect_terms: bool = field(default_factory=lambda: _bool("SGM_RESPECT_TERMS", True))
    # Serve cached responses only; never hit the network (offline demos and tests).
    offline: bool = field(default_factory=lambda: _bool("SGM_OFFLINE", False))
    cache_dir: Path = field(
        default_factory=lambda: Path(
            os.getenv("SGM_CACHE_DIR", Path.home() / ".cache" / "swiss-grounding-mcp")
        )
    )
    timeout_s: float = field(default_factory=lambda: float(os.getenv("SGM_HTTP_TIMEOUT", "15")))
    # Minimum seconds between two requests to the same host (source etiquette).
    min_interval_s: float = field(default_factory=lambda: float(os.getenv("SGM_MIN_INTERVAL", "0.5")))
    # Some Swiss sites serve an empty shell to non-browser agents, so we send a browser-compatible
    # string that still identifies this project and links to it.
    user_agent: str = field(
        default_factory=lambda: os.getenv(
            "SGM_USER_AGENT",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36 "
            f"SwissGroundingMCP/{VERSION} (+{REPO_URL})",
        )
    )
    robots_token: str = "SwissGroundingMCP"


settings = Settings()
