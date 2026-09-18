"""
FastAPI application entry point.

Run locally:

    cd backend
    uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

Interactive docs land on ``/docs``.
"""
from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from typing import Any, Dict

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import get_settings
from .datasource import get_ephemeris_source
from .predictor import EclipsePredictor, get_predictor
from .routers import night, system

log = logging.getLogger("eclipse_hunter")

START_TIME = time.time()


def _configure_logging(level: str) -> None:
    # IMPORTANT: import astroquery *before* touching logging.getLogger("astroquery").
    #
    # astroquery's `_init_log()` does:
    #     logging.setLoggerClass(AstropyLogger); log = logging.getLogger('astroquery')
    #     log._set_defaults()
    # `setLoggerClass` only affects loggers that do not exist yet. If we call
    # `logging.getLogger("astroquery")` first (to quiet it down), Python caches a
    # plain `logging.Logger`, astroquery then gets that cached object back, and
    # `_set_defaults()` raises AttributeError -- which would silently disable
    # SIMBAD verification for the entire process. Real bug, found the hard way.
    try:
        import astroquery  # noqa: F401
    except Exception:
        pass

    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    # astroquery/astropy are chatty at INFO level.
    for noisy in ("astroquery", "astropy", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Warm the ephemeris catalogue at boot.

    Doing this at startup (rather than on the first user request) means:
      * the first request is fast, and
      * the ~1 MB download happens once per container, not once per worker race.

    Failures are non-fatal: the API still boots and will retry lazily.
    """
    settings = get_settings()
    _configure_logging(settings.log_level)
    log.info("Starting %s v%s", settings.app_name, settings.app_version)
    try:
        bundle = get_ephemeris_source().get_bundle()
        log.info(
            "Ephemeris ready: %d stars (%d element records) from %s",
            len(bundle.stars), bundle.element_records,
            "cache" if bundle.from_cache else "network",
        )
    except Exception as exc:
        log.error("Ephemeris pre-load failed (%s); will retry on first request", exc)

    # Import/configure astroquery on the main thread. Doing it lazily from the
    # SIMBAD thread pool can race the interpreter's import machinery.
    try:
        from .simbad import get_simbad_client

        if get_simbad_client().preconfigure():
            log.info("SIMBAD client ready")
    except Exception as exc:
        log.warning("SIMBAD client pre-configuration failed: %s", exc)
    yield
    log.info("Shutting down")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description=settings.description,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
        max_age=600,
    )

    @app.middleware("http")
    async def add_timing_header(request: Request, call_next):
        started = time.monotonic()
        response = await call_next(request)
        response.headers["X-Process-Time-Ms"] = str(int((time.monotonic() - started) * 1000))
        return response

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        log.exception("Unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={
                "detail": "Internal server error",
                "type": type(exc).__name__,
                "path": request.url.path,
            },
        )

    app.include_router(system.router)
    app.include_router(night.router)

    app.state.started_at = START_TIME
    return app


app = create_app()


def predictor_dependency() -> EclipsePredictor:
    return get_predictor()


__all__ = ["app", "create_app", "predictor_dependency"]
