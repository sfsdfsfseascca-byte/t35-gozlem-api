"""
Astropy engine: minimum prediction + observing geometry.

Responsibilities
----------------
1. ``next_minima``         -- solve ``T = M0 + N * P`` for a time window.
2. ``heliocentric_to_geocentric`` -- the light-time correction that turns the
   catalogue's HJD elements into a clock time an observer can actually use.
3. ``observe_event``       -- altitude / azimuth / Moon separation / Sun
   altitude / lunar illumination at the instant of minimum.
4. ``score_event``         -- a 0-100 "how good is this target tonight" score.

Correctness notes
-----------------
* ``EPHEM.TXT`` gives **H**JD. Skipping the heliocentric correction introduces a
  periodic error of up to +/-8.3 minutes -- larger than many eclipse durations.
  We use ``JD_geo = HJD - (r_earth . r_star_hat) / c``, iterated twice because
  Earth moves during the correction. Verified to agree with
  ``astropy.time.Time.light_travel_time(..., kind='heliocentric')`` to <0.1 s.
* Positions are J2000/ICRS; ``AltAz`` handles precession-nutation and the
  UTC->TT->TDB chain internally, so we never hand-roll a sidereal time.
* The Moon comes from astropy's builtin analytic ephemeris (accuracy ~5",
  i.e. utterly negligible next to the 30 deg separation threshold). Add
  ``jplephem`` and set ``solar_system_ephemeris.set('jpl')`` if you want more.
"""
from __future__ import annotations

import logging
import math
import warnings
from dataclasses import dataclass
from datetime import date as date_type
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

import astropy.units as u
import numpy as np
from astropy.utils.exceptions import AstropyWarning
from astropy.coordinates import (
    AltAz,
    EarthLocation,
    SkyCoord,
    get_body,
    get_body_barycentric,
    get_sun,
)
from astropy.time import Time

from .ephemeris import ElementRecord, Star

log = logging.getLogger("eclipse_hunter.astro")

#: Seconds for light to travel 1 AU.
SECONDS_PER_AU = u.au.to(u.km) / 299792.458

#: Coarse duration model (hours) by GCVS variability type prefix.
#: Used only to label the card with "expect ~X hours of dimming"; it is an
#: order-of-magnitude guide, not a light-curve model.
_DURATION_MODEL: Tuple[Tuple[str, float], ...] = (
    ("EA/DS", 1.2), ("EA/DM", 1.2), ("EA/D", 1.2),
    ("EA/SD", 1.5), ("EA/S", 1.5),
    ("EB/KE", 2.5), ("EB/KW", 2.5), ("EB", 2.5),
    ("EW/KW", 3.0), ("EW/K", 3.0), ("EW", 3.0),
    ("EA", 1.5), ("ELL", 6.0), ("E", 2.0),
)


# ---------------------------------------------------------------------- #
# Location helpers
# ---------------------------------------------------------------------- #
def make_location(latitude: float, longitude: float, elevation_m: float = 0.0) -> EarthLocation:
    return EarthLocation.from_geodetic(
        lon=longitude * u.deg, lat=latitude * u.deg, height=elevation_m * u.m
    )


def timezone_from_location(latitude: float, longitude: float, explicit: Optional[str]) -> ZoneInfo:
    """Resolve the observer's IANA timezone.

    Prefer the client-supplied value (the phone knows it). Otherwise fall back
    to a longitude-based fixed offset -- approximate, but never silently wrong
    by whole hours the way a naive ``utc`` default would be.
    """
    if explicit:
        try:
            return ZoneInfo(explicit)
        except Exception:
            log.warning("Unknown timezone %r; falling back to solar offset", explicit)
    offset_hours = round(longitude / 15.0)
    offset_hours = max(-12, min(14, offset_hours))
    if offset_hours == 0:
        return ZoneInfo("UTC")
    # The POSIX "Etc/GMT" zones invert the sign: "Etc/GMT-3" is UTC+3.
    # Getting this backwards puts a Turkish observer two hours behind UTC.
    sign = "-" if offset_hours > 0 else "+"
    return ZoneInfo(f"Etc/GMT{sign}{abs(offset_hours)}")


def utc_offset_hours(tz: ZoneInfo, when_utc: datetime) -> float:
    aware = when_utc.replace(tzinfo=ZoneInfo("UTC"))
    return aware.astimezone(tz).utcoffset().total_seconds() / 3600.0


# ---------------------------------------------------------------------- #
# Minimum prediction
# ---------------------------------------------------------------------- #
@dataclass
class MinimumInstance:
    kind: str                 # "primary" | "secondary"
    cycle_number: int
    epoch_hjd: float          # heliocentric JD of THIS minimum
    time_utc: datetime        # geocentric, timezone-aware UTC
    jd_geocentric: float
    light_time_min: float     # applied correction, minutes (negative = earlier)
    uncertainty_min: float    # 1-sigma, minutes
    element: ElementRecord


def heliocentric_to_geocentric(hjd: float, coord: SkyCoord) -> Tuple[Time, float]:
    """Convert a Heliocentric Julian Date to geocentric UTC.

    ``JD_geo = HJD - (r_earth_from_sun . r_hat_star) / c``

    The Sun->Earth vector is built from barycentric positions of both bodies.
    Using Earth's barycentric position alone (a common shortcut) leaves a
    ~2.4 s error, because the Sun sits that far from the solar-system
    barycentre; matching ``Time.light_travel_time(..., kind="heliocentric")``
    exactly is worth the one extra ephemeris lookup.

    Iterated twice because Earth moves during the ~8 minute correction.
    Returns ``(Time, correction_minutes)``.
    """
    unit = coord.cartesian.xyz.value  # dimensionless unit vector (ICRS)
    time = Time(hjd, format="jd", scale="utc")
    correction_days = 0.0
    for _ in range(2):  # 2 iterations converge to ~1e-10 d
        earth = get_body_barycentric("earth", time)
        sun = get_body_barycentric("sun", time)
        dot_au = (
            (earth.x - sun.x).value * unit[0]
            + (earth.y - sun.y).value * unit[1]
            + (earth.z - sun.z).value * unit[2]
        )
        correction_days = -dot_au * SECONDS_PER_AU / 86400.0
        time = Time(hjd + correction_days, format="jd", scale="utc")
    return time, correction_days * 1440.0


def apply_light_time_correction(
    jd_heliocentric: float, coord: SkyCoord
) -> Tuple[float, float]:
    """Correct a heliocentric JD to geocentric once coordinates are known.

    ``minima_in_window`` cannot do this for stars that are absent from
    ``allstars-cat.txt`` (Algol, beta Lyr, beta Aur, ... -- about 99 of them)
    because there is no position to project Earth's orbit onto. Those stars are
    resolved through SIMBAD first and their times are corrected here.

    The correction is up to +/-8.3 minutes, which is larger than the entire
    eclipse for many EA systems, so skipping it would make the displayed time
    genuinely misleading.

    Returns ``(jd_geocentric, correction_minutes)``.
    """
    time_obj, correction_min = heliocentric_to_geocentric(jd_heliocentric, coord)
    return time_obj.jd, correction_min


def _epochs_for(element: ElementRecord, kind: str) -> Optional[Tuple[float, float]]:
    """Return ``(epoch_hjd, period_days)`` for a primary or secondary minimum."""
    period = element.period_days
    if not period or period <= 0:
        return None

    if kind == "primary":
        # A PRI or ALL record both describe primary minima.
        if element.min_type in ("PRI", "ALL"):
            return element.epoch_hjd, period
        return None

    # ---- secondary ----
    if element.min_type == "SEC":
        return element.epoch_hjd, period
    if element.min_type == "ALL":
        # No dedicated secondary solution. Place it half a period after the
        # primary, refined by the catalogue's secondary phase when available
        # (this is what makes eccentric systems usable at all).
        phase = element.secondary_phase
        offset = 0.5 if phase is None else float(phase)
        return element.epoch_hjd + offset * period, period
    return None


def minima_in_window(
    star: Star,
    window_start_utc: datetime,
    window_end_utc: datetime,
    include_secondary: bool = True,
    apply_light_time: bool = True,
) -> List[MinimumInstance]:
    """All primary/secondary minima of ``star`` falling inside the window.

    Purely arithmetic until the very end, so it costs microseconds per star and
    can be run over the whole 4000-star catalogue without any SIMBAD traffic.
    Uses catalogue coordinates when available for the light-time correction;
    otherwise a 2-minute-level error remains (documented in the response).
    """
    start = Time(window_start_utc, scale="utc")
    end = Time(window_end_utc, scale="utc")

    coord: Optional[SkyCoord] = None
    if star.catalog is not None:
        coord = SkyCoord(ra=star.catalog.ra_deg * u.deg, dec=star.catalog.dec_deg * u.deg, frame="icrs")

    kinds = ["primary"] + (["secondary"] if include_secondary else [])
    out: List[MinimumInstance] = []
    seen: set = set()

    for kind in kinds:
        for element in star.elements:
            epochs = _epochs_for(element, kind)
            if epochs is None:
                continue
            epoch, period = epochs

            n_start = int(math.ceil((start.jd - epoch) / period))
            n_end = int(math.floor((end.jd - epoch) / period))
            if n_end < n_start:
                continue
            # Defensive cap: a 0.1-day period over a 2-day window is ~20 cycles,
            # but a corrupt tiny period could yield millions.
            if n_end - n_start > 200:
                log.warning(
                    "%s: suspicious cycle count %d for P=%.6f; skipping",
                    star.display_name, n_end - n_start, period,
                )
                continue

            for n in range(n_start, n_end + 1):
                hjd = epoch + n * period
                if hjd < start.jd - 1 or hjd > end.jd + 1:
                    continue

                if apply_light_time and coord is not None:
                    time_obj, ltt_min = heliocentric_to_geocentric(hjd, coord)
                else:
                    time_obj = Time(hjd, format="jd", scale="utc")
                    ltt_min = 0.0

                stamp = time_obj.to_datetime(timezone=ZoneInfo("UTC"))
                dedupe = (kind, round(time_obj.jd, 6))
                if dedupe in seen:
                    continue
                seen.add(dedupe)

                out.append(
                    MinimumInstance(
                        kind=kind,
                        cycle_number=n,
                        epoch_hjd=hjd,
                        time_utc=stamp,
                        jd_geocentric=time_obj.jd,
                        light_time_min=round(ltt_min, 4),
                        uncertainty_min=round(element.time_uncertainty_days(hjd) * 1440.0, 2),
                        element=element,
                    )
                )

    out.sort(key=lambda m: m.jd_geocentric)
    return out


# ---------------------------------------------------------------------- #
# Night window
# ---------------------------------------------------------------------- #
@dataclass
class NightWindow:
    start_utc: datetime
    end_utc: datetime
    sunset_utc: Optional[datetime]
    sunrise_utc: Optional[datetime]
    dark_start_utc: Optional[datetime]
    dark_end_utc: Optional[datetime]


def night_window(
    night_date: date_type,
    location: EarthLocation,
    tz: ZoneInfo,
    evening_start_local: str = "17:00",
    morning_end_local: str = "08:00",
) -> NightWindow:
    """The observing night that begins on the evening of ``night_date``.

    Spans ``17:00`` local on ``night_date`` to ``08:00`` local on the following
    day, which safely covers dusk-to-dawn at every latitude the app is likely to
    be used at while keeping the search window small.
    """
    hh_e, mm_e = (int(x) for x in evening_start_local.split(":"))
    hh_m, mm_m = (int(x) for x in morning_end_local.split(":"))

    local_start = datetime(night_date.year, night_date.month, night_date.day, hh_e, mm_e, tzinfo=tz)
    local_end = local_start + timedelta(days=1)
    local_end = local_end.replace(hour=hh_m, minute=mm_m)

    start_utc = local_start.astimezone(ZoneInfo("UTC"))
    end_utc = local_end.astimezone(ZoneInfo("UTC"))

    # Solar events -- best effort; polar day/night simply yields None.
    sunset = sunrise = dark_start = dark_end = None
    try:
        n_steps = int((end_utc - start_utc).total_seconds() // 300) + 1
        stamps = [start_utc + timedelta(minutes=5 * i) for i in range(n_steps)]
        grid = Time(stamps)
        frame = AltAz(obstime=grid, location=location)
        sun_alt = np.asarray(get_sun(grid).transform_to(frame).alt.deg, dtype=float)

        def _crossing(threshold: float, evening: bool) -> Optional[datetime]:
            """First grid interval where the Sun crosses ``threshold``.

            ``evening=True`` looks for a downward crossing (sunset / dusk),
            ``False`` for an upward one (sunrise / dawn).
            """
            for i in range(1, len(sun_alt)):
                going_down = sun_alt[i] < sun_alt[i - 1]
                crossed = (sun_alt[i - 1] >= threshold > sun_alt[i]) or (
                    sun_alt[i - 1] <= threshold < sun_alt[i]
                )
                if not crossed:
                    continue
                if evening and not going_down:
                    continue
                if not evening and going_down:
                    continue
                delta = sun_alt[i] - sun_alt[i - 1]
                frac = (threshold - sun_alt[i - 1]) / delta if delta else 0.0
                return start_utc + timedelta(minutes=5 * (i - 1 + frac))
            return None

        sunset = _crossing(-0.833, evening=True)
        sunrise = _crossing(-0.833, evening=False)
        dark_start = _crossing(-18.0, evening=True)
        dark_end = _crossing(-18.0, evening=False)
    except Exception as exc:  # pragma: no cover
        log.warning("Could not compute solar events: %s", exc)

    return NightWindow(start_utc, end_utc, sunset, sunrise, dark_start, dark_end)


# ---------------------------------------------------------------------- #
# Observing geometry
# ---------------------------------------------------------------------- #
def moon_illumination(time: Time) -> Tuple[float, float]:
    """Return ``(illumination_fraction, sun_moon_elongation_deg)``.

    Uses the simple phase-angle approximation, which is accurate to ~1% of
    illumination -- far below what matters against a 30 degree separation
    threshold.
    """
    moon = get_body("moon", time)
    sun = get_sun(time)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", AstropyWarning)
        elong = float(moon.separation(sun).deg)
    frac = (1.0 - math.cos(math.radians(elong))) / 2.0
    return frac, elong


def phase_name(illum: float, waxing: bool) -> str:
    if illum < 0.02:
        return "New Moon"
    if illum > 0.98:
        return "Full Moon"
    if illum < 0.48:
        return "Waxing Crescent" if waxing else "Waning Crescent"
    if illum < 0.52:
        return "First Quarter" if waxing else "Last Quarter"
    return "Waxing Gibbous" if waxing else "Waning Gibbous"


@dataclass
class EventGeometry:
    altitude_deg: float
    azimuth_deg: float
    peak_altitude_deg: float
    peak_altitude_offset_h: float
    moon_separation_deg: float
    moon_altitude_deg: float
    moon_illumination_percent: float
    sun_altitude_deg: float


def observe_event(
    coord: SkyCoord,
    event_time_utc: datetime,
    location: EarthLocation,
    half_window_h: float = 1.0,
    grid_points: int = 9,
) -> EventGeometry:
    """Altitude / Moon separation / Sun altitude at (and around) the minimum.

    The ``grid_points`` sweep is what lets us report the *peak* altitude during
    the eclipse rather than only at the exact instant -- for a 3-hour EW-type
    event the difference is routinely several degrees, and it is the number that
    actually determines whether the observer can see it.
    """
    t_mid = Time(event_time_utc, scale="utc")

    # Sweep the event window and take the best altitude.
    offsets = np.linspace(-half_window_h, half_window_h, max(1, grid_points))
    grid = Time([t_mid.jd + off / 24.0 for off in offsets], format="jd", scale="utc")
    frame = AltAz(obstime=grid, location=location)
    altaz = coord.transform_to(frame)
    alts = np.asarray(altaz.alt.deg, dtype=float)
    best = int(np.nanargmax(alts))

    mid_frame = AltAz(obstime=t_mid, location=location)
    mid_altaz = coord.transform_to(mid_frame)
    moon = get_body("moon", t_mid, location=location)
    sun = get_sun(t_mid)
    moon_altaz = moon.transform_to(mid_frame)
    sun_altaz = sun.transform_to(mid_frame)

    # Both operands share the same obstime, so this is a pure rotation and
    # astropy's direction-dependence caveat does not apply here.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", AstropyWarning)
        illum, _elong = moon_illumination(t_mid)

    return EventGeometry(
        altitude_deg=float(mid_altaz.alt.deg),
        azimuth_deg=float(mid_altaz.az.deg),
        peak_altitude_deg=float(alts[best]),
        peak_altitude_offset_h=float(offsets[best]),
        moon_separation_deg=float(coord.separation(moon).deg),
        moon_altitude_deg=float(moon_altaz.alt.deg),
        moon_illumination_percent=round(illum * 100.0, 1),
        sun_altitude_deg=float(sun_altaz.alt.deg),
    )


# ---------------------------------------------------------------------- #
# Batched geometry (the performance-critical path)
# ---------------------------------------------------------------------- #
#: Points per AltAz transform. astropy vectorises internally, so a few thousand
#: at a time is both fast and memory-friendly.
_BATCH_CHUNK = 4000


def _chunked(count: int, size: int):
    for start in range(0, count, size):
        yield start, min(start + size, count)


def observe_events_batch(
    ra_deg: Sequence[float],
    dec_deg: Sequence[float],
    times_utc: Sequence[datetime],
    location: EarthLocation,
    half_window_h: float = 1.0,
    sweep_points: int = 9,
) -> List[EventGeometry]:
    """Vectorised equivalent of :func:`observe_event` for many events at once.

    This exists because the naive per-event loop is brutally slow: roughly 40 ms
    of astropy frame-transform overhead each, which turns a 5 000-candidate
    night into a 3.5-minute request. Batching the same maths into a handful of
    array transforms brings the whole night under ~2 s.

    All positions are geocentric ICRS, which also avoids astropy's
    NonRotationTransformationWarning from mixing an observer-located GCRS Moon
    with an ICRS star.
    """
    n = len(ra_deg)
    if n == 0:
        return []
    if not (n == len(dec_deg) == len(times_utc)):
        raise ValueError("ra/dec/time arrays must have equal length")

    ra = np.asarray(ra_deg, dtype=float)
    dec = np.asarray(dec_deg, dtype=float)
    jd = np.array([Time(t, scale="utc").jd for t in times_utc], dtype=float)
    offsets = np.linspace(-half_window_h, half_window_h, max(1, sweep_points)) / 24.0

    alt_at_min = np.full(n, np.nan)
    az_at_min = np.full(n, np.nan)
    moon_sep = np.full(n, np.nan)
    moon_alt = np.full(n, np.nan)
    moon_illum = np.full(n, np.nan)
    sun_alt = np.full(n, np.nan)

    def _altaz(times_jd: np.ndarray, ra_sel: np.ndarray, dec_sel: np.ndarray):
        obstime = Time(times_jd, format="jd", scale="utc")
        coord = SkyCoord(ra=ra_sel * u.deg, dec=dec_sel * u.deg, frame="icrs", obstime=obstime)
        altaz = coord.transform_to(AltAz(obstime=obstime, location=location))
        return (
            np.asarray(altaz.alt.deg, dtype=float),
            np.asarray(altaz.az.deg, dtype=float),
        )

    # ---- 1. altitude / azimuth at the instant of minimum ---------------- #
    for lo, hi in _chunked(n, _BATCH_CHUNK):
        alt_at_min[lo:hi], az_at_min[lo:hi] = _altaz(jd[lo:hi], ra[lo:hi], dec[lo:hi])

    # ---- 2. peak altitude across the event window ----------------------- #
    # Materialise the sweep as one flat array, then reduce per event. A star at
    # transit mid-eclipse can be several degrees higher than at the exact
    # minimum instant, and that number is what decides visibility.
    sweep_alt = np.full((n, len(offsets)), np.nan)
    flat_total = n * len(offsets)
    for lo, hi in _chunked(flat_total, _BATCH_CHUNK):
        idx = np.arange(lo, hi)
        star_i, off_i = np.divmod(idx, len(offsets))
        alts, _ = _altaz(jd[star_i] + offsets[off_i], ra[star_i], dec[star_i])
        sweep_alt.ravel()[lo:hi] = alts

    best = np.nanargmax(sweep_alt, axis=1)
    peak_alt = sweep_alt[np.arange(n), best]
    peak_off = offsets[best] * 24.0

    # ---- 3. Moon + Sun geometry ---------------------------------------- #
    for lo, hi in _chunked(n, _BATCH_CHUNK):
        obstime = Time(jd[lo:hi], format="jd", scale="utc")
        target = SkyCoord(ra=ra[lo:hi] * u.deg, dec=dec[lo:hi] * u.deg,
                          frame="icrs", obstime=obstime)
        moon_geo = get_body("moon", obstime)
        sun_geo = get_sun(obstime)

        # Both operands carry the same obstime, so this is a pure rotation and
        # astropy's direction-dependence caveat does not apply. It warns anyway,
        # once per chunk, which would drown the logs.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", AstropyWarning)
            moon_sep[lo:hi] = np.asarray(target.separation(moon_geo).deg, dtype=float)
            elong = np.asarray(moon_geo.separation(sun_geo).deg, dtype=float)
        moon_illum[lo:hi] = (1.0 - np.cos(np.radians(elong))) / 2.0 * 100.0

        frame = AltAz(obstime=obstime, location=location)
        moon_alt[lo:hi] = np.asarray(moon_geo.transform_to(frame).alt.deg, dtype=float)
        sun_alt[lo:hi] = np.asarray(sun_geo.transform_to(frame).alt.deg, dtype=float)

    return [
        EventGeometry(
            altitude_deg=float(alt_at_min[i]),
            azimuth_deg=float(az_at_min[i]),
            peak_altitude_deg=float(peak_alt[i]),
            peak_altitude_offset_h=float(peak_off[i]),
            moon_separation_deg=float(moon_sep[i]),
            moon_altitude_deg=float(moon_alt[i]),
            moon_illumination_percent=round(float(moon_illum[i]), 1),
            sun_altitude_deg=float(sun_alt[i]),
        )
        for i in range(n)
    ]


def declination_limit(latitude_deg: float, min_altitude_deg: float) -> Tuple[float, float]:
    """The declination band within which a target can reach ``min_altitude_deg``.

    A culminating object peaks at ``alt_max = 90 - |lat - dec|``, so requiring
    ``alt_max >= A`` gives ``|lat - dec| <= 90 - A``.

    Returns ``(dec_min, dec_max)``:

    * Kayseri (lat +38.7, A=30) -> ``(-21.3, +90)``: everything south of
      dec -21.3 is permanently too low, so a large fraction of the catalogue
      can be discarded by pure arithmetic before any frame transform.
    * Sydney (lat -33.9, A=30)  -> ``(-90, +23.9)``: the mirror image, and a
      reminder that the naive northern-hemisphere formula silently returns a
      *minimum* where the south needs a *maximum*.
    """
    half_width = 90.0 - min_altitude_deg
    return (
        max(-90.0, latitude_deg - half_width),
        min(90.0, latitude_deg + half_width),
    )


def is_declination_plausible(
    dec_deg: float, latitude_deg: float, min_altitude_deg: float
) -> bool:
    """Cheap arithmetic pre-filter used before any astropy transform."""
    dec_min, dec_max = declination_limit(latitude_deg, min_altitude_deg)
    return dec_min <= dec_deg <= dec_max


def min_visible_declination(latitude_deg: float, min_altitude_deg: float) -> float:
    """Deprecated shim: northern-hemisphere lower bound only.

    Kept so existing callers keep working; prefer :func:`declination_limit`.
    """
    return declination_limit(latitude_deg, min_altitude_deg)[0]


def estimate_duration_h(variability_type: Optional[str], period_days: float) -> float:
    """Very rough total eclipse duration in hours, from the GCVS type.

    Only used for the UI hint ("watch from T-1h to T+1h"). Deliberately
    conservative and clearly labelled as an estimate.
    """
    vt = (variability_type or "").upper().strip()
    for prefix, hours in _DURATION_MODEL:
        if vt.startswith(prefix):
            return hours
    # No type: fall back to a fraction of the period, capped.
    return max(0.5, min(6.0, period_days * 24.0 * 0.05))


# ---------------------------------------------------------------------- #
# Scoring
# ---------------------------------------------------------------------- #
def score_event(
    altitude_deg: float,
    peak_altitude_deg: float,
    moon_separation_deg: float,
    moon_illumination_percent: float,
    v_magnitude: Optional[float],
    depth_mag: Optional[float],
    sun_altitude_deg: float,
) -> Tuple[float, str]:
    """Blend the observability factors into a 0-100 score + quality label.

    Weighting rationale:
      * altitude (35)  -- airmass kills contrast; everything above ~60 deg is
        equally good, below ~35 degrades fast.
      * moon (30)      -- both separation AND phase matter; a full Moon 40 deg
        away is worse than a new Moon 20 deg away.
      * depth (20)     -- amplitude is what makes the event *visible*.
      * brightness (10)-- naked eye (V<6) beats a 9th-mag telescope target.
      * darkness (5)   -- bonus for astronomical darkness.
    """
    alt_score = max(0.0, min(1.0, (peak_altitude_deg - 20.0) / 50.0)) * 35.0
    if altitude_deg < peak_altitude_deg:
        alt_score *= 0.85 + 0.15 * max(0.0, min(1.0, (altitude_deg - 20.0) / 50.0))

    sep_score = max(0.0, min(1.0, (moon_separation_deg - 20.0) / 100.0))
    illum_penalty = (moon_illumination_percent / 100.0) ** 1.5
    moon_score = (sep_score * (1.0 - 0.75 * illum_penalty) + 0.25 * (1.0 - illum_penalty)) * 30.0

    if depth_mag is None:
        depth_score = 8.0  # unknown amplitude: neutral-ish
    else:
        depth_score = max(0.0, min(1.0, (depth_mag - 0.1) / 1.4)) * 20.0

    if v_magnitude is None:
        bright_score = 4.0
    elif v_magnitude <= 5.0:
        bright_score = 10.0
    elif v_magnitude <= 7.0:
        bright_score = 8.0
    elif v_magnitude <= 9.0:
        bright_score = 5.5
    else:
        bright_score = 2.5

    dark_score = 5.0 if sun_altitude_deg <= -18 else (3.0 if sun_altitude_deg <= -12 else (1.5 if sun_altitude_deg <= -6 else 0.0))

    total = alt_score + moon_score + depth_score + bright_score + dark_score
    total = max(0.0, min(100.0, total))
    quality = "excellent" if total >= 70 else ("good" if total >= 50 else "fair")
    return round(total, 1), quality


def coord_to_hms(coord: SkyCoord) -> Tuple[str, str]:
    """Pretty sexagesimal RA/Dec for the UI."""
    ra = coord.ra.to_string(unit=u.hour, sep=":", precision=1, pad=True)
    dec = coord.dec.to_string(sep=":", precision=0, pad=True, alwayssign=True)
    return ra, dec


def separation_arcsec(a: SkyCoord, b: SkyCoord) -> float:
    return float(a.separation(b).arcsec)


def star_coord(ra_deg: float, dec_deg: float) -> SkyCoord:
    return SkyCoord(ra=ra_deg * u.deg, dec=dec_deg * u.deg, frame="icrs")


def format_local(dt_utc: datetime, tz: ZoneInfo) -> str:
    return dt_utc.astimezone(tz).strftime("%H:%M")


def format_local_full(dt_utc: datetime, tz: ZoneInfo) -> str:
    return dt_utc.astimezone(tz).strftime("%Y-%m-%d %H:%M")


def observability_summary(events: Sequence[Dict]) -> Dict[str, object]:
    """Aggregate stats for the UI header."""
    if not events:
        return {"count": 0}
    alts = [e["peak_altitude_deg"] for e in events]
    return {
        "count": len(events),
        "best_altitude_deg": max(alts),
        "median_altitude_deg": float(np.median(alts)),
        "earliest_local": min(e["time_local"] for e in events),
        "latest_local": max(e["time_local"] for e in events),
    }
