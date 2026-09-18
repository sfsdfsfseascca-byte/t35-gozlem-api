"""
Prediction endpoints.

Two equivalent shapes are offered because mobile clients differ in taste:

* ``GET  /api/v1/night``  -- query-string friendly, easy to open in a browser
  or curl while debugging.
* ``POST /api/v1/night``  -- the full JSON body, which is what the Flutter app
  uses. Preferred because it round-trips every filter without URL-encoding
  surprises.
"""
from __future__ import annotations

import logging
from datetime import date as date_type
from datetime import datetime, timezone as dt_timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ..cache import get_cache, prediction_key
from ..config import Settings, get_settings
from ..deps import rate_limit, require_api_key
from ..models import LocationIn, NightQuery, NightResponse
from ..predictor import EclipsePredictor, get_predictor

log = logging.getLogger("eclipse_hunter.api.night")

router = APIRouter(prefix="/api/v1", tags=["predictions"])


# ---------------------------------------------------------------------- #
# Cache wrapper
# ---------------------------------------------------------------------- #
def _cache_key(query: NightQuery) -> str:
    payload: Dict[str, Any] = query.model_dump(mode="json")
    # force_refresh must not change the identity of the result.
    payload.pop("force_refresh", None)
    return prediction_key(payload)


def _read_cache(query: NightQuery) -> Optional[NightResponse]:
    if query.force_refresh:
        return None
    raw = get_cache().get_json(_cache_key(query))
    if not raw:
        return None
    try:
        return NightResponse.model_validate(raw)
    except Exception as exc:  # pragma: no cover - schema drift
        log.warning("Cached night response failed validation: %s", exc)
        return None


def _write_cache(query: NightQuery, response: NightResponse) -> None:
    settings = get_settings()
    if settings.prediction_ttl <= 0:
        return
    try:
        get_cache().set_json(
            _cache_key(query),
            response.model_dump(mode="json"),
            settings.prediction_ttl,
        )
    except Exception as exc:  # pragma: no cover
        log.warning("Could not cache night response: %s", exc)


def _run(query: NightQuery, predictor: EclipsePredictor) -> NightResponse:
    cached = _read_cache(query)
    if cached is not None:
        log.info("Night cache hit for %s", query.date)
        return cached

    try:
        response = predictor.predict(query)
    except RuntimeError as exc:
        # Ephemeris download/parse failure -> 503 so the client can retry.
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        log.exception("Prediction failed")
        raise HTTPException(status_code=500, detail=f"Prediction failed: {exc}") from exc

    _write_cache(query, response)
    return response


# ---------------------------------------------------------------------- #
# POST (primary)
# ---------------------------------------------------------------------- #
@router.post(
    "/night",
    response_model=NightResponse,
    summary="Predict observable eclipsing-binary minima for one night",
    dependencies=[Depends(rate_limit), Depends(require_api_key)],
)
def predict_night(
    query: NightQuery,
    predictor: EclipsePredictor = Depends(get_predictor),
) -> NightResponse:
    """Returns every primary/secondary minimum that is worth observing.

    Applied filters (all overridable in the request body):

    | filter | default | meaning |
    |---|---|---|
    | ``min_altitude_deg`` | 30 | target must reach this altitude during the eclipse |
    | ``min_moon_separation_deg`` | 30 | target must be this far from the Moon |
    | ``max_vmag`` | 9.5 | brightest-magnitude limit (naked eye ~6.5) |
    | ``min_depth_mag`` | 0.3 | minimum eclipse amplitude in magnitudes |
    | ``max_sun_altitude_deg`` | -6 | Sun must be below this (civil twilight) |
    """
    return _run(query, predictor)


# ---------------------------------------------------------------------- #
# GET (convenience / debugging)
# ---------------------------------------------------------------------- #
@router.get(
    "/night",
    response_model=NightResponse,
    summary="Same as POST /night, using query parameters",
    dependencies=[Depends(rate_limit), Depends(require_api_key)],
)
def predict_night_get(
    date: date_type = Query(..., description="Local observing date, YYYY-MM-DD"),
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
    elevation_m: float = Query(0.0, ge=-500, le=9000),
    timezone: Optional[str] = Query(None, description="IANA tz, e.g. Europe/Istanbul"),
    min_altitude: float = Query(30.0, ge=0, le=89),
    min_moon_sep: float = Query(30.0, ge=0, le=180),
    max_vmag: float = Query(9.5, ge=1, le=18),
    min_depth: float = Query(0.3, ge=0, le=6),
    max_sun_alt: float = Query(-6.0, ge=-90, le=10),
    include_secondary: bool = Query(True),
    require_dark_sky: bool = Query(True),
    use_simbad: bool = Query(True),
    max_results: int = Query(150, ge=1, le=500),
    sort_by: str = Query("time", pattern="^(time|altitude|score|magnitude)$"),
    force_refresh: bool = Query(False),
    predictor: EclipsePredictor = Depends(get_predictor),
) -> NightResponse:
    settings = get_settings()
    max_results = min(max_results, settings.hard_max_results)
    query = NightQuery(
        date=date,
        location=LocationIn(
            latitude=lat, longitude=lon, elevation_m=elevation_m, timezone=timezone
        ),
        min_altitude_deg=min_altitude,
        min_moon_separation_deg=min_moon_sep,
        max_vmag=max_vmag,
        min_depth_mag=min_depth,
        max_sun_altitude_deg=max_sun_alt,
        include_secondary=include_secondary,
        require_dark_sky=require_dark_sky,
        use_simbad=use_simbad,
        max_results=max_results,
        sort_by=sort_by,  # type: ignore[arg-type]
        force_refresh=force_refresh,
    )
    return _run(query, predictor)


# ---------------------------------------------------------------------- #
# Helpers used by the mobile app
# ---------------------------------------------------------------------- #
@router.get(
    "/defaults",
    summary="Server-side default filter values",
    dependencies=[Depends(rate_limit)],
)
def get_defaults() -> Dict[str, Any]:
    """Lets the app pre-fill its filter sheet from the server, so changing a
    default is a deploy on the backend rather than an app-store release."""
    s = get_settings()
    return {
        "min_altitude_deg": s.default_min_altitude,
        "min_moon_separation_deg": s.default_min_moon_sep,
        "max_vmag": s.default_max_vmag,
        "min_depth_mag": s.default_min_depth_mag,
        "max_sun_altitude_deg": s.default_max_sun_alt,
        "max_results": s.default_max_results,
        "hard_max_results": s.hard_max_results,
        "simbad_enabled": s.simbad_enabled,
        "rate_limit_per_minute": s.rate_limit_per_minute,
    }


@router.get(
    "/tonight",
    summary="Quick call: tonight's best targets for a location",
    dependencies=[Depends(rate_limit), Depends(require_api_key)],
)
def predict_tonight(
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
    timezone: Optional[str] = Query(None),
    max_results: int = Query(25, ge=1, le=100),
    predictor: EclipsePredictor = Depends(get_predictor),
) -> NightResponse:
    """Convenience wrapper for the app's one-tap 'Tonight' button."""
    s = get_settings()
    from ..astro_engine import timezone_from_location

    tz = timezone_from_location(lat, lon, timezone)
    today = datetime.now(dt_timezone.utc).astimezone(tz).date()
    query = NightQuery(
        date=today,
        location=LocationIn(latitude=lat, longitude=lon, timezone=str(tz)),
        min_altitude_deg=s.default_min_altitude,
        min_moon_separation_deg=s.default_min_moon_sep,
        max_vmag=s.default_max_vmag,
        min_depth_mag=s.default_min_depth_mag,
        max_sun_altitude_deg=s.default_max_sun_alt,
        max_results=min(max_results, s.hard_max_results),
        sort_by="score",
    )
    return _run(query, predictor)
