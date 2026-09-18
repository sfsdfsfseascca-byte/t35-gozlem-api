"""System endpoints: health, cache stats, catalogue info, admin refresh."""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Query

from ..cache import get_cache
from ..config import get_settings
from ..datasource import get_ephemeris_source
from ..deps import rate_limit, require_api_key
from ..ephemeris import catalog_stats
from ..models import CatalogStatsResponse, EphemerisInfo, HealthResponse, SimbadQuery, SimbadResponse
from ..simbad import get_simbad_client

log = logging.getLogger("eclipse_hunter.api.system")

router = APIRouter(tags=["system"])

#: Process start, used for the /health uptime figure.
_START = time.time()


@router.get("/", include_in_schema=False)
def root() -> Dict[str, Any]:
    s = get_settings()
    return {
        "service": s.app_name,
        "version": s.app_version,
        "docs": "/docs",
        "health": "/health",
        "endpoints": [
            "POST /api/v1/night",
            "GET  /api/v1/night",
            "GET  /api/v1/tonight",
            "GET  /api/v1/defaults",
            "GET  /api/v1/catalog/stats",
            "GET  /api/v1/simbad/{identifier}",
            "POST /api/v1/admin/refresh-ephemeris",
        ],
    }


@router.get("/health", response_model=HealthResponse, summary="Liveness + dependency status")
def health() -> HealthResponse:
    settings = get_settings()
    cache = get_cache()
    source = get_ephemeris_source()
    bundle = source._bundle  # noqa: SLF001 - cheap introspection, no load triggered

    cache_stats = cache.stats()
    # A cheap Redis round-trip so /health actually reflects cache availability.
    if cache.name == "redis":
        try:
            cache.set_json("eh:healthcheck", {"ok": True}, 10)
            cache_stats["reachable"] = cache.get_json("eh:healthcheck") is not None
        except Exception:
            cache_stats["reachable"] = False

    loaded = bundle is not None
    degraded = not loaded or (cache.name == "redis" and cache_stats.get("reachable") is False)
    return HealthResponse(
        status="degraded" if degraded else "ok",
        version=settings.app_version,
        uptime_s=round(time.time() - _START, 1),
        cache=cache_stats,
        ephemeris_loaded=loaded,
        ephemeris_stars=len(bundle.stars) if loaded else 0,
        utc_now=datetime.now(timezone.utc),
    )


@router.get(
    "/api/v1/catalog/stats",
    response_model=CatalogStatsResponse,
    summary="Ephemeris catalogue statistics",
    dependencies=[Depends(rate_limit)],
)
def catalog_statistics(
    limit: int = Query(20, ge=1, le=200, description="How many brightest stars to list"),
) -> CatalogStatsResponse:
    bundle = get_ephemeris_source().get_bundle()
    stats = catalog_stats(bundle.stars)

    brightest: List[Dict[str, Any]] = []
    for star in bundle.stars.values():
        if star.catalog is None or star.catalog.v_max is None:
            continue
        brightest.append({
            "name": star.display_name,
            "constellation": star.key.constellation,
            "v_max": star.catalog.v_max,
            "v_min": star.catalog.v_min,
            "depth_mag": star.catalog.depth_mag,
            "variability_type": star.catalog.variability_type,
            "period_days": star.primary_element.period_days if star.primary_element else None,
        })
    brightest.sort(key=lambda row: row["v_max"])

    return CatalogStatsResponse(
        element_records=int(stats["element_records"]),
        unique_stars=int(stats["unique_stars"]),
        with_catalog_data=int(stats["with_catalog_data"]),
        brightest_stars=brightest[:limit],
        ephemeris=EphemerisInfo(
            source_url=bundle.source_url,
            downloaded_at=bundle.downloaded_at,
            content_hash=bundle.content_hash,
            element_records=bundle.element_records,
            unique_stars=len(bundle.stars),
            from_cache=bundle.from_cache,
        ),
    )


@router.get(
    "/api/v1/simbad/{identifier}",
    response_model=SimbadResponse,
    summary="Resolve one identifier through the cached SIMBAD ladder",
    dependencies=[Depends(rate_limit)],
)
def resolve_simbad(
    identifier: str,
    force_refresh: bool = Query(False),
) -> SimbadResponse:
    """Debug/inspection helper. Goes through the same cache as the predictor,
    so calling it warms the cache for real requests."""
    client = get_simbad_client()
    cached = None if force_refresh else client.cache_get(identifier)
    if cached is not None:
        return SimbadResponse(
            identifier=identifier, found=cached.found, from_cache=True,
            negative=cached.negative, ra_deg=cached.ra_deg, dec_deg=cached.dec_deg,
            v_mag=cached.v_mag, main_id=cached.main_id, object_type=cached.object_type,
            spectral_type=cached.spectral_type, tried=cached.tried, reason=cached.reason,
        )
    result = client.resolve([identifier], force_refresh=force_refresh)
    return SimbadResponse(
        identifier=identifier, found=result.found, from_cache=False,
        negative=result.negative, ra_deg=result.ra_deg, dec_deg=result.dec_deg,
        v_mag=result.v_mag, main_id=result.main_id, object_type=result.object_type,
        spectral_type=result.spectral_type, tried=result.tried, reason=result.reason,
    )


@router.post(
    "/api/v1/admin/refresh-ephemeris",
    summary="Force a re-download of EPHEM.TXT + allstars-cat.txt",
    dependencies=[Depends(rate_limit), Depends(require_api_key)],
)
def refresh_ephemeris() -> Dict[str, Any]:
    """Protected by ``EH_API_KEY`` when one is configured."""
    source = get_ephemeris_source()
    try:
        bundle = source.get_bundle(force_refresh=True)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Refresh failed: {exc}") from exc
    return {
        "refreshed": True,
        "content_hash": bundle.content_hash,
        "unique_stars": len(bundle.stars),
        "element_records": bundle.element_records,
        "catalog_records": bundle.catalog_records,
        "downloaded_at": bundle.downloaded_at.isoformat(),
    }
