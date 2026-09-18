"""Pydantic schemas shared by the API layer and the prediction engine."""
from __future__ import annotations

from datetime import date as date_type
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

EclipseKind = Literal["primary", "secondary"]
CoordSource = Literal["simbad", "catalog", "none"]


# ---------------------------------------------------------------------- #
# Requests
# ---------------------------------------------------------------------- #
class LocationIn(BaseModel):
    """Observer location.

    ``timezone`` is an IANA name (e.g. ``Europe/Istanbul``). If omitted, the
    backend derives a reasonable fixed offset from the longitude so results are
    never silently wrong by hours.
    """

    latitude: float = Field(..., ge=-90.0, le=90.0, description="Degrees north")
    longitude: float = Field(..., ge=-180.0, le=180.0, description="Degrees east")
    elevation_m: float = Field(0.0, ge=-500.0, le=9000.0)
    timezone: Optional[str] = Field(
        None, description="IANA tz, e.g. Europe/Istanbul. Auto-derived if absent."
    )
    label: Optional[str] = Field(None, description="Human readable place name")

    @field_validator("timezone")
    @classmethod
    def _validate_tz(cls, v: Optional[str]) -> Optional[str]:
        if not v:
            return None
        import zoneinfo

        try:
            zoneinfo.ZoneInfo(v)
        except Exception as exc:
            raise ValueError(f"Unknown IANA timezone '{v}': {exc}") from exc
        return v


class NightQuery(BaseModel):
    """Query for "which eclipses can I see on this night?"."""

    date: date_type = Field(..., description="Local observing date (evening of)")
    location: LocationIn
    min_altitude_deg: float = Field(30.0, ge=0.0, le=89.0)
    min_moon_separation_deg: float = Field(30.0, ge=0.0, le=180.0)
    max_vmag: float = Field(9.5, ge=1.0, le=18.0)
    min_depth_mag: float = Field(0.3, ge=0.0, le=6.0)
    max_sun_altitude_deg: float = Field(
        -6.0, ge=-90.0, le=10.0, description="-6 civil / -12 nautical / -18 astronomical"
    )
    include_secondary: bool = True
    require_dark_sky: bool = Field(
        True, description="Drop events that occur while the Sun is above max_sun_altitude_deg"
    )
    use_simbad: bool = Field(
        True, description="Resolve coordinates/V-mag via SIMBAD (cached). False = catalog only."
    )
    event_half_window_h: float = Field(
        1.0, ge=0.0, le=6.0,
        description="Half-width of the window swept when computing peak altitude",
    )
    max_results: int = Field(150, ge=1, le=500)
    sort_by: Literal["time", "altitude", "score", "magnitude"] = "time"
    force_refresh: bool = False

    @field_validator("date")
    @classmethod
    def _validate_date(cls, v: date_type) -> date_type:
        from datetime import date, timedelta

        if v < date(1970, 1, 1):
            raise ValueError("date must be 1970-01-01 or later")
        if v > date.today() + timedelta(days=365 * 5):
            raise ValueError("date is too far in the future")
        return v


class SimbadQuery(BaseModel):
    identifier: str = Field(..., min_length=1, max_length=64)
    force_refresh: bool = False


# ---------------------------------------------------------------------- #
# Responses
# ---------------------------------------------------------------------- #
class EphemerisInfo(BaseModel):
    source_url: str
    downloaded_at: Optional[datetime] = None
    content_hash: str = ""
    element_records: int = 0
    unique_stars: int = 0
    from_cache: bool = False
    citation: str = (
        "Kreiner, J.M. 2004, 'Up-to-date Linear Elements of Close Binaries', "
        "Acta Astronomica 54, 207. Database: TIDAK, Astronomy Department, "
        "University of National Education Commission, Krakow."
    )


class MoonInfo(BaseModel):
    illumination_percent: float
    phase_name: str
    elongation_deg: float
    altitude_deg: Optional[float] = None
    azimuth_deg: Optional[float] = None


class NightWindow(BaseModel):
    start_utc: datetime
    end_utc: datetime
    local_start: str
    local_end: str
    timezone: str
    utc_offset_hours: float
    sunset_utc: Optional[datetime] = None
    sunrise_utc: Optional[datetime] = None
    astronomical_dark_start_utc: Optional[datetime] = None
    astronomical_dark_end_utc: Optional[datetime] = None


class CatalogInfo(BaseModel):
    """Where the coordinates / magnitude came from."""

    ra_deg: Optional[float] = None
    dec_deg: Optional[float] = None
    source: CoordSource = "none"
    simbad_main_id: Optional[str] = None
    simbad_object_type: Optional[str] = None
    simbad_spectral_type: Optional[str] = None
    simbad_v_mag: Optional[float] = None
    v_mag: Optional[float] = None
    v_max: Optional[float] = None
    v_min: Optional[float] = None
    depth_mag: Optional[float] = None
    crossmatch_arcsec: Optional[float] = None
    warnings: List[str] = Field(default_factory=list)


class EclipseEvent(BaseModel):
    # Identity
    star_name: str = Field(..., description="GCVS designation, e.g. 'RZ Cas'")
    constellation: str
    gcvs_name: Optional[str] = None
    raw_name: Optional[str] = None

    # Ephemeris
    kind: EclipseKind
    cycle_number: Optional[int] = None
    period_days: float
    epoch_hjd: float
    time_jd_geocentric: float
    time_utc: datetime
    time_local: str
    time_local_iso: datetime
    time_local_date: str
    time_uncertainty_min: float = Field(
        0.0, description="1-sigma propagated from epoch error + period error"
    )

    # Photometry / geometry
    v_magnitude: Optional[float] = None
    v_max: Optional[float] = None
    v_min: Optional[float] = None
    depth_mag: Optional[float] = None
    variability_type: Optional[str] = None
    spectral_type: Optional[str] = None
    duration_estimate_h: Optional[float] = None

    ra_deg: Optional[float] = None
    dec_deg: Optional[float] = None

    altitude_deg: float = Field(..., description="Altitude at the instant of minimum")
    azimuth_deg: float
    peak_altitude_deg: float = Field(
        ..., description="Best altitude reached within +/- the event window"
    )
    peak_altitude_time_local: Optional[str] = None

    moon_separation_deg: float
    moon_altitude_deg: Optional[float] = None
    moon_illumination_percent: Optional[float] = None
    sun_altitude_deg: Optional[float] = None
    is_dark: bool = True

    # Ranking
    score: float = Field(0.0, description="0-100 observability score")
    quality: Literal["excellent", "good", "fair"] = "fair"

    catalog: CatalogInfo = Field(default_factory=CatalogInfo)


class FilterStats(BaseModel):
    candidates_from_ephemeris: int = 0
    after_brightness_filter: int = 0
    after_local_time_filter: int = 0
    after_altitude_filter: int = 0
    after_moon_filter: int = 0
    after_darkness_filter: int = 0
    unique_stars: int = 0
    returned: int = 0
    simbad_lookups: int = 0
    simbad_cache_hits: int = 0
    simbad_unresolved: int = 0
    simbad_budget_skipped: int = 0
    elapsed_ms: int = 0
    truncated: bool = False


class NightResponse(BaseModel):
    query_date: str
    generated_at: datetime
    location: LocationIn
    window: NightWindow
    moon: MoonInfo
    filters_applied: Dict[str, Any]
    events: List[EclipseEvent]
    stats: FilterStats
    ephemeris: EphemerisInfo
    warnings: List[str] = Field(default_factory=list)


class SimbadResponse(BaseModel):
    identifier: str
    found: bool
    from_cache: bool = False
    negative: bool = False
    ra_deg: Optional[float] = None
    dec_deg: Optional[float] = None
    v_mag: Optional[float] = None
    main_id: Optional[str] = None
    object_type: Optional[str] = None
    spectral_type: Optional[str] = None
    tried: List[str] = Field(default_factory=list)
    reason: Optional[str] = None


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    version: str
    uptime_s: float
    cache: Dict[str, Any]
    ephemeris_loaded: bool
    ephemeris_stars: int
    utc_now: datetime


class CatalogStatsResponse(BaseModel):
    element_records: int
    unique_stars: int
    with_catalog_data: int
    brightest_stars: List[Dict[str, Any]]
    ephemeris: EphemerisInfo
