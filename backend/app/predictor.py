"""
The prediction pipeline: "what can I actually observe on this night?"

Order of operations is deliberately chosen to minimise cost, because the most
expensive step (SIMBAD) is also the one most likely to get us rate-limited::

    4 000 stars in EPHEM.TXT
      │
      ├─ 1. arithmetic minima in the night window     (free, ~10 ms)
      ├─ 2. local-time-of-night filter                (free)
      ├─ 3. altitude / Moon / Sun filter, using the   (free, astropy only)
      │     catalogue coordinates from allstars-cat.txt
      ├─ 4. brightness + eclipse-depth pre-filter     (free)
      │        └──► ~20-60 survivors
      ├─ 5. SIMBAD verification (cached, budgeted)    (the only network cost)
      ├─ 6. re-check geometry with SIMBAD coordinates (catches big offsets)
      └─ 7. score, sort, truncate

Steps 1-4 run on catalogue data alone, so a warm cache makes a request almost
free, and a cold cache still only touches CDS for stars the user could plausibly
observe.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import date as date_type
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import astropy.units as u
from astropy.coordinates import AltAz, SkyCoord, get_body, get_sun
from astropy.time import Time
from astropy.utils.exceptions import AstropyWarning

from .astro_engine import (
    EventGeometry,
    apply_light_time_correction,
    estimate_duration_h,
    format_local,
    make_location,
    declination_limit,
    minima_in_window,
    moon_illumination,
    night_window,
    observe_events_batch,
    phase_name,
    score_event,
    star_coord,
    timezone_from_location,
    utc_offset_hours,
)
from .datasource import EphemerisSource, get_ephemeris_source
from .ephemeris import Star, is_eclipsing_type
from .simbad import _crossmatch_arcsec
from .models import (
    CatalogInfo,
    EclipseEvent,
    EphemerisInfo,
    FilterStats,
    LocationIn,
    MoonInfo,
    NightQuery,
    NightResponse,
    NightWindow,
)
from .simbad import SimbadClient, SimbadResult, get_simbad_client

log = logging.getLogger("eclipse_hunter.predictor")

#: Period range (days) considered predictable and worth observing.
#: Below ~0.2 d a star produces dozens of minima per night and is usually a
#: dwarf nova rather than a visual eclipsing binary; above ~200 d the linear
#: elements are useless for a single-night plan.
MIN_PERIOD_DAYS = 0.2
MAX_PERIOD_DAYS = 200.0


@dataclass
class _Candidate:
    star: Star
    instance: Any                      # MinimumInstance
    coord: SkyCoord
    geometry: EventGeometry
    v_mag: Optional[float]
    depth: Optional[float]
    resolution: Optional[SimbadResult] = None
    warnings: List[str] = field(default_factory=list)


class EclipsePredictor:
    """Stateless service object; all mutable state lives in its dependencies."""

    def __init__(
        self,
        source: Optional[EphemerisSource] = None,
        simbad: Optional[SimbadClient] = None,
    ) -> None:
        self.source = source or get_ephemeris_source()
        self.simbad = simbad or get_simbad_client()

    # ------------------------------------------------------------------ #
    def predict(self, query: NightQuery) -> NightResponse:
        started = time.monotonic()
        warnings: List[str] = []

        bundle = self.source.get_bundle(force_refresh=query.force_refresh)
        loc = make_location(
            query.location.latitude, query.location.longitude, query.location.elevation_m
        )
        tz = timezone_from_location(
            query.location.latitude, query.location.longitude, query.location.timezone
        )
        window = night_window(query.date, loc, tz)

        # Reset the per-request SIMBAD budget so each API call gets its own.
        self.simbad.reset_budget()

        stats = FilterStats()

        # ---------- Stage 1: pure arithmetic ------------------------- #
        raw_candidates: List[Tuple[Star, Any]] = []
        for star in bundle.stars.values():
            instances = minima_in_window(
                star,
                window.start_utc,
                window.end_utc,
                include_secondary=query.include_secondary,
            )
            for inst in instances:
                raw_candidates.append((star, inst))
        stats.candidates_from_ephemeris = len(raw_candidates)

        # ---------- Stage 2: free arithmetic pre-filters --------------- #
        # Everything here costs nanoseconds per candidate and exists purely to
        # shrink the set that needs an astropy transform or (far worse) a
        # network round-trip to SIMBAD.
        dec_min, dec_max = declination_limit(
            query.location.latitude, query.min_altitude_deg
        )

        def _plausibly_visible(dec: float) -> bool:
            """Can this declination ever reach ``min_altitude_deg`` from here?"""
            return dec_min <= dec <= dec_max

        stage2: List[Tuple[Star, Any]] = []
        for star, inst in raw_candidates:
            period = inst.element.period_days
            # Sub-hour periods produce dozens of "minima" per night and are
            # usually dwarf novae rather than usable visual eclipsing binaries;
            # multi-hundred-day periods are never predictable for one night.
            if not (MIN_PERIOD_DAYS <= period <= MAX_PERIOD_DAYS):
                continue
            cat = star.catalog
            if cat is not None:
                if not _plausibly_visible(cat.dec_deg):
                    continue
                if not is_eclipsing_type(cat.variability_type):
                    continue
            # A minimum at 02:00 UTC can be 13:00 local -- useless to observe.
            local_hour = inst.time_utc.astimezone(tz).hour
            if 11 <= local_hour < 16:
                continue
            stage2.append((star, inst))
        stats.after_local_time_filter = len(stage2)

        # ---------- Stage 3: geometry for everything with catalog coords -- #
        # Done BEFORE any SIMBAD traffic. This is the single most important
        # ordering decision in the pipeline: the geometric filters typically
        # remove 95% of candidates, and it would be both slow and rude to CDS
        # to resolve stars we are about to throw away.
        with_catalog = [(s, i) for s, i in stage2 if s.catalog is not None]
        without_catalog = [(s, i) for s, i in stage2 if s.catalog is None]

        geometries = observe_events_batch(
            [s.catalog.ra_deg for s, _ in with_catalog],
            [s.catalog.dec_deg for s, _ in with_catalog],
            [i.time_utc for _, i in with_catalog],
            loc,
            half_window_h=query.event_half_window_h,
            sweep_points=9,
        )

        survivors: List[_Candidate] = []
        for (star, inst), geom in zip(with_catalog, geometries):
            if geom.peak_altitude_deg < query.min_altitude_deg:
                continue
            stats.after_altitude_filter += 1
            if geom.moon_separation_deg < query.min_moon_separation_deg:
                continue
            stats.after_moon_filter += 1
            if query.require_dark_sky and geom.sun_altitude_deg > query.max_sun_altitude_deg:
                continue
            stats.after_darkness_filter += 1
            cat = star.catalog
            assert cat is not None
            if cat.v_max is not None and cat.v_max > query.max_vmag:
                continue
            if cat.depth_mag is not None and cat.depth_mag < query.min_depth_mag:
                continue
            survivors.append(
                _Candidate(
                    star=star, instance=inst,
                    coord=star_coord(cat.ra_deg, cat.dec_deg), geometry=geom,
                    v_mag=cat.v_max, depth=cat.depth_mag,
                )
            )

        # ---------- Stage 4: SIMBAD, only for the survivors ------------- #
        simbad_hits = 0
        simbad_lookups = 0
        resolved_lookup: Dict[Tuple[str, str], SimbadResult] = {}
        want_simbad = query.use_simbad and self.simbad.settings.simbad_enabled

        if not want_simbad:
            warnings.append("SIMBAD lookup disabled; coordinates come from allstars-cat.txt")
            if without_catalog:
                warnings.append(
                    f"{len(without_catalog)} candidate(s) have no catalogue coordinates and "
                    "were skipped because SIMBAD lookup is disabled"
                )
            without_catalog = []
        else:
            # Stars absent from allstars-cat.txt (Algol, beta Lyr, ...) can only
            # be located via SIMBAD. Apply the cheap declination pre-filter
            # first so we do not spend queries on targets that cannot rise.
            extra: List[Tuple[Star, Any]] = []
            for star, inst in without_catalog:
                # We have no declination yet, so only the local-time filter has
                # been applied. Resolve them -- but cap the work.
                extra.append((star, inst))

            unique_jobs: Dict[Tuple[str, str], Dict[str, Any]] = {}
            # Priority 1: stars we will definitely show (verify/enrich them).
            for cand in survivors:
                star_id = cand.star.key.upper
                if star_id in unique_jobs:
                    continue
                unique_jobs[star_id] = {
                    "star_id": star_id,
                    "candidates": cand.star.simbad_identifiers(),
                    "ra_deg": cand.star.catalog.ra_deg if cand.star.catalog else None,
                    "dec_deg": cand.star.catalog.dec_deg if cand.star.catalog else None,
                }
            # Priority 2: catalogue-less stars, best budget permitting.
            for star, _inst in extra:
                star_id = star.key.upper
                if star_id in unique_jobs:
                    continue
                unique_jobs[star_id] = {
                    "star_id": star_id,
                    "candidates": star.simbad_identifiers(),
                    "ra_deg": None,
                    "dec_deg": None,
                }

            before = self.simbad.queries_issued
            resolved_lookup = self.simbad.resolve_many(unique_jobs.values())
            simbad_lookups = self.simbad.queries_issued - before
            simbad_hits = sum(1 for r in resolved_lookup.values() if r.found)

            for cand in survivors:
                cand.resolution = resolved_lookup.get(cand.star.key.upper)

            # Now compute geometry for the catalogue-less stars we resolved.
            resolved_extra: List[Tuple[Star, Any, SimbadResult]] = []
            for star, inst in extra:
                res = resolved_lookup.get(star.key.upper)
                if res is None or not res.found or res.ra_deg is None or res.dec_deg is None:
                    if res is not None and res.budget_exhausted:
                        stats.simbad_budget_skipped += 1
                    else:
                        stats.simbad_unresolved += 1
                    continue
                if not _plausibly_visible(res.dec_deg):
                    continue
                resolved_extra.append((star, inst, res))

            if resolved_extra:
                # These stars had no catalogue position, so minima_in_window
                # could not apply the heliocentric->geocentric light-time
                # correction. Now that SIMBAD has given us coordinates, fix the
                # event times (up to +/-8.3 min) before computing geometry.
                corrected_times: List[datetime] = []
                for star, inst, res in resolved_extra:
                    coord = star_coord(res.ra_deg, res.dec_deg)
                    jd_geo, ltt_min = apply_light_time_correction(inst.epoch_hjd, coord)
                    inst.jd_geocentric = jd_geo
                    inst.light_time_min = round(ltt_min, 4)
                    inst.time_utc = Time(jd_geo, format="jd", scale="utc").to_datetime(
                        timezone=ZoneInfo("UTC")
                    )
                    corrected_times.append(inst.time_utc)

                extra_geom = observe_events_batch(
                    [r.ra_deg for _s, _i, r in resolved_extra],
                    [r.dec_deg for _s, _i, r in resolved_extra],
                    corrected_times,
                    loc,
                    half_window_h=query.event_half_window_h,
                    sweep_points=9,
                )
                for (star, inst, res), geom in zip(resolved_extra, extra_geom):
                    if geom.peak_altitude_deg < query.min_altitude_deg:
                        continue
                    if geom.moon_separation_deg < query.min_moon_separation_deg:
                        continue
                    if query.require_dark_sky and geom.sun_altitude_deg > query.max_sun_altitude_deg:
                        continue
                    if res.v_mag is not None and res.v_mag > query.max_vmag:
                        continue
                    cand = _Candidate(
                        star=star, instance=inst,
                        coord=star_coord(res.ra_deg, res.dec_deg), geometry=geom,
                        v_mag=res.v_mag, depth=None, resolution=res,
                        warnings=["coordinates from SIMBAD (absent in allstars-cat.txt)"],
                    )
                    survivors.append(cand)

        stats.after_brightness_filter = len(survivors)

        # ---------- Stage 5: upgrade coordinates from SIMBAD ------------ #
        # Any position shift small enough to pass the cross-match guard moves
        # the altitude by at most a few hundredths of a degree, so the geometry
        # computed in stage 3 stays valid and we only refresh metadata here.
        final: List[_Candidate] = []
        for cand in survivors:
            res = cand.resolution
            cat = cand.star.catalog
            if res is not None and res.found and res.ra_deg is not None and cat is not None:
                sep = _crossmatch_arcsec(
                    res.ra_deg, res.dec_deg, cat.ra_deg, cat.dec_deg
                )
                limit = self.simbad.settings.simbad_max_crossmatch_arcsec
                if sep is not None and sep > limit:
                    # SIMBAD resolved the name to something in a different part
                    # of the sky. Keep the catalogue position (it is what the
                    # ephemeris belongs to) but tell the user.
                    cand.warnings.append(
                        f"SIMBAD match '{res.main_id or res.identifier}' is {sep/3600.0:.1f} deg "
                        "from the catalogue position; catalogue coordinates used"
                    )
                else:
                    cand.coord = star_coord(res.ra_deg, res.dec_deg)
                    if sep is not None:
                        res.crossmatch_arcsec = round(sep, 2)
                    if cand.v_mag is None and res.v_mag is not None:
                        cand.v_mag = res.v_mag
                    if cand.depth is None and res.v_mag is not None and cat.v_min is not None:
                        if res.v_mag < cat.v_min:
                            cand.depth = round(cat.v_min - res.v_mag, 3)
            final.append(cand)

        # ---------- Stage 7: dedupe, score, sort ----------------------- #
        # A star can appear twice (primary + secondary) -- keep both, that is
        # genuinely two observing opportunities. But collapse exact duplicates
        # that arise when EPHEM.TXT lists both PRI and ALL for the same star.
        deduped: Dict[Tuple[str, str, str, int], _Candidate] = {}
        for cand in final:
            key = (
                cand.star.key.upper[0], cand.star.key.upper[1],
                cand.instance.kind, round(cand.instance.jd_geocentric * 1440.0),
            )
            existing = deduped.get(key)
            if existing is None or cand.geometry.peak_altitude_deg > existing.geometry.peak_altitude_deg:
                deduped[key] = cand
        final = list(deduped.values())
        stats.unique_stars = len({(c.star.key.upper) for c in final})

        events = [self._to_event(cand, tz, query) for cand in final]
        events.sort(key=lambda e: self._sort_key(e, query.sort_by))

        truncated = len(events) > query.max_results
        if truncated:
            events = events[: query.max_results]
        stats.returned = len(events)
        stats.truncated = truncated
        stats.simbad_lookups = simbad_lookups
        stats.simbad_cache_hits = simbad_hits
        stats.elapsed_ms = int((time.monotonic() - started) * 1000)

        if stats.simbad_unresolved:
            warnings.append(
                f"{stats.simbad_unresolved} candidate(s) could not be resolved and were dropped"
            )
        if stats.simbad_budget_skipped:
            warnings.append(
                f"{stats.simbad_budget_skipped} candidate(s) were not checked against SIMBAD "
                "because the per-request query budget was reached; they may still be "
                "observable. Re-run to resolve more."
            )

        return NightResponse(
            query_date=query.date.isoformat(),
            generated_at=datetime.now(ZoneInfo("UTC")),
            location=query.location,
            window=self._window_model(window, tz),
            moon=self._moon_model(window, loc),
            filters_applied={
                "min_altitude_deg": query.min_altitude_deg,
                "min_moon_separation_deg": query.min_moon_separation_deg,
                "max_vmag": query.max_vmag,
                "min_depth_mag": query.min_depth_mag,
                "max_sun_altitude_deg": query.max_sun_altitude_deg,
                "include_secondary": query.include_secondary,
                "require_dark_sky": query.require_dark_sky,
                "use_simbad": query.use_simbad,
            },
            events=events,
            stats=stats,
            ephemeris=EphemerisInfo(
                source_url=bundle.source_url,
                downloaded_at=bundle.downloaded_at,
                content_hash=bundle.content_hash,
                element_records=bundle.element_records,
                unique_stars=len(bundle.stars),
                from_cache=bundle.from_cache,
            ),
            warnings=warnings,
        )

    # ------------------------------------------------------------------ #
    # Builders
    # ------------------------------------------------------------------ #
    def _to_event(self, cand: _Candidate, tz: ZoneInfo, query: NightQuery) -> EclipseEvent:
        star, inst, geom = cand.star, cand.instance, cand.geometry
        cat = star.catalog
        res = cand.resolution

        local_dt = inst.time_utc.astimezone(tz)
        peak_local = (inst.time_utc + timedelta(hours=geom.peak_altitude_offset_h)).astimezone(tz)

        v_mag = cand.v_mag
        v_max = cat.v_max if cat else None
        v_min = cat.v_min if cat else None
        depth = cand.depth
        if depth is None and v_max is not None and v_min is not None and v_min > v_max:
            depth = round(v_min - v_max, 3)

        score, quality = score_event(
            altitude_deg=geom.altitude_deg,
            peak_altitude_deg=geom.peak_altitude_deg,
            moon_separation_deg=geom.moon_separation_deg,
            moon_illumination_percent=geom.moon_illumination_percent,
            v_magnitude=v_mag,
            depth_mag=depth,
            sun_altitude_deg=geom.sun_altitude_deg,
        )

        coord_source = "simbad" if (res and res.source in ("simbad", "vizier")) else (
            "catalog" if (res and res.source == "catalog") or cat else "none"
        )

        event_warnings: List[str] = []
        event_warnings.extend(cand.warnings)
        if inst.uncertainty_min > 30:
            event_warnings.append(
                f"timing uncertain by ~{inst.uncertainty_min:.0f} min (old epoch + period drift)"
            )
        if res and res.reason and res.source == "catalog":
            event_warnings.append(res.reason)

        return EclipseEvent(
            star_name=star.display_name,
            constellation=star.key.constellation,
            gcvs_name=res.main_id if res and res.main_id else star.pretty_name,
            raw_name=f"{star.key.name} {star.key.constellation}",
            kind=inst.kind,
            cycle_number=inst.cycle_number,
            period_days=round(inst.element.period_days, 8),
            epoch_hjd=round(inst.epoch_hjd, 5),
            time_jd_geocentric=round(inst.jd_geocentric, 6),
            time_utc=inst.time_utc,
            time_local=format_local(inst.time_utc, tz),
            time_local_iso=local_dt,
            time_local_date=local_dt.date().isoformat(),
            time_uncertainty_min=inst.uncertainty_min,
            v_magnitude=round(v_mag, 2) if v_mag is not None else None,
            v_max=v_max,
            v_min=v_min,
            depth_mag=depth,
            variability_type=(cat.variability_type if cat else None),
            spectral_type=(
                (res.spectral_type if res and res.spectral_type else None)
                or (cat.spectral_type if cat else None)
                or None
            ),
            duration_estimate_h=estimate_duration_h(
                cat.variability_type if cat else None, inst.element.period_days
            ),
            ra_deg=round(cand.coord.ra.deg, 6) if cand.coord is not None else None,
            dec_deg=round(cand.coord.dec.deg, 6) if cand.coord is not None else None,
            altitude_deg=round(geom.altitude_deg, 1),
            azimuth_deg=round(geom.azimuth_deg, 1),
            peak_altitude_deg=round(geom.peak_altitude_deg, 1),
            peak_altitude_time_local=format_local(
                inst.time_utc + timedelta(hours=geom.peak_altitude_offset_h), tz
            ),
            moon_separation_deg=round(geom.moon_separation_deg, 1),
            moon_altitude_deg=round(geom.moon_altitude_deg, 1),
            moon_illumination_percent=geom.moon_illumination_percent,
            sun_altitude_deg=round(geom.sun_altitude_deg, 1),
            is_dark=geom.sun_altitude_deg <= query.max_sun_altitude_deg,
            score=score,
            quality=quality,
            catalog=CatalogInfo(
                ra_deg=round(cand.coord.ra.deg, 6) if cand.coord is not None else None,
                dec_deg=round(cand.coord.dec.deg, 6) if cand.coord is not None else None,
                source=coord_source,  # type: ignore[arg-type]
                simbad_main_id=res.main_id if res else None,
                simbad_object_type=res.object_type if res else None,
                simbad_spectral_type=res.spectral_type if res else None,
                simbad_v_mag=res.v_mag if res else None,
                v_mag=round(v_mag, 2) if v_mag is not None else None,
                v_max=v_max,
                v_min=v_min,
                depth_mag=depth,
                crossmatch_arcsec=(
                    round(res.crossmatch_arcsec, 1)
                    if res and res.crossmatch_arcsec is not None else None
                ),
                warnings=event_warnings,
            ),
        )

    @staticmethod
    def _sort_key(event: EclipseEvent, sort_by: str) -> Any:
        if sort_by == "altitude":
            return (-event.peak_altitude_deg, event.time_local_iso)
        if sort_by == "score":
            return (-event.score, event.time_local_iso)
        if sort_by == "magnitude":
            return (event.v_magnitude if event.v_magnitude is not None else 99.0,
                    event.time_local_iso)
        return (event.time_local_iso, -event.peak_altitude_deg)

    @staticmethod
    def _window_model(window, tz: ZoneInfo) -> NightWindow:
        return NightWindow(
            start_utc=window.start_utc,
            end_utc=window.end_utc,
            local_start=window.start_utc.astimezone(tz).strftime("%Y-%m-%d %H:%M"),
            local_end=window.end_utc.astimezone(tz).strftime("%Y-%m-%d %H:%M"),
            timezone=str(tz),
            utc_offset_hours=utc_offset_hours(tz, window.start_utc),
            sunset_utc=window.sunset_utc,
            sunrise_utc=window.sunrise_utc,
            astronomical_dark_start_utc=window.dark_start_utc,
            astronomical_dark_end_utc=window.dark_end_utc,
        )

    @staticmethod
    def _moon_model(window, loc) -> MoonInfo:
        mid = Time(window.start_utc + (window.end_utc - window.start_utc) / 2, scale="utc")
        illum, elong = moon_illumination(mid)
        # Waxing if the Moon is east of the Sun (elongation measured 0->180 is
        # ambiguous, so use the ecliptic longitude difference).
        waxing = True
        try:
            from astropy.coordinates import GeocentricTrueEcliptic

            moon_lon = get_body("moon", mid).transform_to(GeocentricTrueEcliptic(equinox=mid)).lon.deg
            sun_lon = get_sun(mid).transform_to(GeocentricTrueEcliptic(equinox=mid)).lon.deg
            waxing = ((moon_lon - sun_lon) % 360.0) < 180.0
        except Exception:  # pragma: no cover
            pass
        frame = AltAz(obstime=mid, location=loc)
        moon_alt = float(get_body("moon", mid, location=loc).transform_to(frame).alt.deg)
        moon_az = float(get_body("moon", mid, location=loc).transform_to(frame).az.deg)
        return MoonInfo(
            illumination_percent=round(illum * 100.0, 1),
            phase_name=phase_name(illum, waxing),
            elongation_deg=round(elong, 1),
            altitude_deg=round(moon_alt, 1),
            azimuth_deg=round(moon_az, 1),
        )


def _separation_arcsec(a: SkyCoord, b: SkyCoord) -> float:
    return float(a.separation(b).arcsec)


# ---------------------------------------------------------------------- #
# Singleton
# ---------------------------------------------------------------------- #
_PREDICTOR: Optional[EclipsePredictor] = None


def get_predictor() -> EclipsePredictor:
    global _PREDICTOR
    if _PREDICTOR is None:
        _PREDICTOR = EclipsePredictor()
    return _PREDICTOR
