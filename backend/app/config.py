"""
Central configuration for the Eclipse Hunter API.

Every value can be overridden with an environment variable (see `.env.example`).
Nothing in this module performs I/O at import time, so importing `app.config`
is cheap and side-effect free -- which keeps unit tests fast.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import List, Optional


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "y"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _env_list(name: str, default: str) -> List[str]:
    return [item.strip() for item in os.getenv(name, default).split(",") if item.strip()]


# Absolute path of the backend package root (…/backend/app)
APP_DIR = Path(__file__).resolve().parent
BACKEND_DIR = APP_DIR.parent
DATA_DIR = Path(os.getenv("EH_DATA_DIR", BACKEND_DIR / "data"))


@dataclass
class Settings:
    # ------------------------------------------------------------------ #
    # Service metadata
    # ------------------------------------------------------------------ #
    app_name: str = "Eclipse Hunter API"
    app_version: str = "1.0.0"
    description: str = (
        "Predicts visually observable eclipsing-binary minima for a given night "
        "and location. Ephemerides: Kreiner (2004) TIDAK database, Astronomy "
        "Department, University of National Education Commission, Krakow."
    )
    # Comma separated list of allowed CORS origins. Use "*" only for dev.
    cors_origins: List[str] = field(
        default_factory=lambda: _env_list("EH_CORS_ORIGINS", "*")
    )
    # Optional shared bearer token. Empty => auth disabled (fine for a public API).
    api_key: str = os.getenv("EH_API_KEY", "")

    # ------------------------------------------------------------------ #
    # Ephemeris source (Krakow / TIDAK)
    # ------------------------------------------------------------------ #
    ephem_base_url: str = os.getenv(
        "EH_EPHEM_BASE_URL", "https://www.as.up.krakow.pl/ephem/"
    )
    # The file the user asked for: raw linear elements (M0 / Period) per star.
    ephem_txt_name: str = os.getenv("EH_EPHEM_TXT", "EPHEM.TXT")
    # Companion file: coordinates, Vmax/Vmin, spectral + variability type.
    # Strongly recommended -- it lets us pre-filter by brightness *before*
    # spending any SIMBAD queries.
    allstars_name: str = os.getenv("EH_ALLSTARS_TXT", "allstars-cat.txt")
    ephem_http_timeout: float = _env_float("EH_EPHEM_HTTP_TIMEOUT", 45.0)
    ephem_cache_ttl: int = _env_int("EH_EPHEM_CACHE_TTL", 24 * 3600)
    # If a download is available locally, use it instead of the network.
    ephem_local_dir: str = os.getenv("EH_EPHEM_LOCAL_DIR", str(DATA_DIR))
    # Polite User-Agent. Never scrape anonymously -- the site operators
    # deserve to know who is hitting them and how to reach you.
    user_agent: str = os.getenv(
        "EH_USER_AGENT",
        "EclipseHunter/1.0 (+https://github.com/yourname/eclipse-hunter; contact: you@example.com)",
    )

    # ------------------------------------------------------------------ #
    # Cache backend
    # ------------------------------------------------------------------ #
    # "redis" | "memory" | "auto"  (auto = redis if EH_REDIS_URL is set)
    cache_backend: str = os.getenv("EH_CACHE_BACKEND", "auto")
    redis_url: str = os.getenv("EH_REDIS_URL", "redis://localhost:6379/0")
    memory_cache_max_items: int = _env_int("EH_MEMORY_CACHE_MAX_ITEMS", 20000)
    # SIMBAD results change essentially never -> cache hard.
    simbad_ttl: int = _env_int("EH_SIMBAD_TTL", 45 * 24 * 3600)
    # Negative cache: keep "not found" answers too, otherwise a missing star
    # is re-queried on every single request (the #1 way to get IP-banned).
    simbad_negative_ttl: int = _env_int("EH_SIMBAD_NEGATIVE_TTL", 12 * 3600)
    # Night predictions are deterministic -> cache them.
    prediction_ttl: int = _env_int("EH_PREDICTION_TTL", 6 * 3600)
    # Randomised +/- fraction added to TTLs to avoid a cache stampede.
    ttl_jitter: float = _env_float("EH_TTL_JITTER", 0.1)

    # ------------------------------------------------------------------ #
    # SIMBAD / astroquery
    # ------------------------------------------------------------------ #
    simbad_enabled: bool = _env_bool("EH_SIMBAD_ENABLED", True)
    simbad_timeout: float = _env_float("EH_SIMBAD_TIMEOUT", 30.0)
    simbad_max_workers: int = _env_int("EH_SIMBAD_MAX_WORKERS", 12)
    simbad_max_retries: int = _env_int("EH_SIMBAD_MAX_RETRIES", 3)
    simbad_retry_backoff: float = _env_float("EH_SIMBAD_RETRY_BACKOFF", 2.0)
    # Hard ceiling of *live* SIMBAD queries for a single API request.
    # Anything above this is served from cache or falls back to the catalog.
    simbad_max_queries_per_request: int = _env_int("EH_SIMBAD_MAX_QUERIES", 250)
    # Reject a SIMBAD match whose position disagrees with the catalog by more
    # than this (arcsec). Guards against SIMBAD resolving "R CMa" to a
    # similarly named but unrelated object.
    simbad_max_crossmatch_arcsec: float = _env_float("EH_SIMBAD_MAX_XMATCH", 300.0)

    # ------------------------------------------------------------------ #
    # Observation filters (defaults; overridable per request)
    # ------------------------------------------------------------------ #
    default_min_altitude: float = _env_float("EH_DEFAULT_MIN_ALTITUDE", 30.0)
    default_min_moon_sep: float = _env_float("EH_DEFAULT_MIN_MOON_SEP", 30.0)
    default_max_vmag: float = _env_float("EH_DEFAULT_MAX_VMAG", 9.5)
    default_min_depth_mag: float = _env_float("EH_DEFAULT_MIN_DEPTH", 0.3)
    # Sun altitude that must be reached for the event to count as "night".
    # -6 = civil twilight, -12 = nautical, -18 = astronomical.
    default_max_sun_alt: float = _env_float("EH_DEFAULT_MAX_SUN_ALT", -6.0)
    # Half duration (hours) assumed around the minimum when computing the
    # "peak altitude" of the event window.
    default_event_half_window_h: float = _env_float("EH_DEFAULT_EVENT_HALF_WINDOW_H", 1.0)
    default_max_results: int = _env_int("EH_DEFAULT_MAX_RESULTS", 150)
    # Absolute safety cap.
    hard_max_results: int = _env_int("EH_HARD_MAX_RESULTS", 500)

    # ------------------------------------------------------------------ #
    # Runtime
    # ------------------------------------------------------------------ #
    log_level: str = os.getenv("EH_LOG_LEVEL", "INFO")
    rate_limit_per_minute: int = _env_int("EH_RATE_LIMIT_PER_MINUTE", 30)
    request_timeout_guard_s: float = _env_float("EH_REQUEST_GUARD_S", 25.0)

    # ------------------------------------------------------------------ #
    @property
    def resolved_cache_backend(self) -> str:
        if self.cache_backend == "auto":
            return "redis" if self.redis_url and "localhost" not in self.redis_url else "memory"
        return self.cache_backend

    @property
    def ephem_txt_url(self) -> str:
        return self.ephem_base_url.rstrip("/") + "/" + self.ephem_txt_name

    @property
    def allstars_url(self) -> str:
        return self.ephem_base_url.rstrip("/") + "/" + self.allstars_name


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """FastAPI dependency. Cached so env parsing happens once."""
    return Settings()
