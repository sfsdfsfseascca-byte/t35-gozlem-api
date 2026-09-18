"""Tests for the astropy engine: minimum prediction, light-time correction, geometry.

The headline test validates the whole timing chain against an independent,
authoritative source rather than against our own arithmetic.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import astropy.units as u
import numpy as np
import pytest
from astropy.coordinates import AltAz, EarthLocation, SkyCoord, get_body_barycentric
from astropy.time import Time

from app.astro_engine import (
    SECONDS_PER_AU,
    apply_light_time_correction,
    estimate_duration_h,
    heliocentric_to_geocentric,
    make_location,
    declination_limit,
    is_declination_plausible,
    min_visible_declination,
    minima_in_window,
    moon_illumination,
    night_window,
    observe_event,
    observe_events_batch,
    phase_name,
    score_event,
    star_coord,
    timezone_from_location,
)
from app.ephemeris import ElementRecord, Star, StarKey

UTC = ZoneInfo("UTC")

# Sky coordinates cross-checked against SIMBAD on 2026-09-18.
ALGOL_RA, ALGOL_DEC = 47.04221855625, 40.95564667027778
ALGOL_M0, ALGOL_PERIOD = 2452500.172, 2.867338


def _algol_star() -> Star:
    element = ElementRecord(
        key=StarKey(name="BET", constellation="Per"),
        min_type="PRI",
        epoch_hjd=ALGOL_M0,
        period_days=ALGOL_PERIOD,
        epoch_err_days=0.005,
        period_err_days=3e-6,
    )
    return Star(key=element.key, elements=[element])


# ---------------------------------------------------------------------- #
# Light-time correction
# ---------------------------------------------------------------------- #
class TestLightTime:
    def test_matches_astropy_builtin(self):
        """Our correction must agree with astropy's own heliocentric LTT.

        astropy's value is the amount to ADD to geocentric to get heliocentric,
        so the signs are opposite.
        """
        coord = star_coord(ALGOL_RA, ALGOL_DEC)
        hjd = ALGOL_M0 + ALGOL_PERIOD * 3064
        geocentric = EarthLocation.from_geocentric(0 * u.km, 0 * u.km, 0 * u.km)
        reference = Time(hjd, format="jd", scale="utc", location=geocentric)
        astropy_ltt_min = reference.light_travel_time(coord, "heliocentric").to_value(u.min)

        _time_obj, our_correction_min = heliocentric_to_geocentric(hjd, coord)
        assert our_correction_min == pytest.approx(-astropy_ltt_min, abs=0.01)

    def test_correction_is_within_physical_bounds(self):
        """The heliocentric correction can never exceed +/-8.3 minutes."""
        coord = star_coord(ALGOL_RA, ALGOL_DEC)
        for cycle in (2900, 3000, 3064, 3100):
            _t, minutes = heliocentric_to_geocentric(ALGOL_M0 + ALGOL_PERIOD * cycle, coord)
            assert abs(minutes) < 8.4

    def test_correction_varies_with_earth_position(self):
        coord = star_coord(ALGOL_RA, ALGOL_DEC)
        values = [
            heliocentric_to_geocentric(ALGOL_M0 + ALGOL_PERIOD * n, coord)[1]
            for n in range(3064, 3075)
        ]
        # Over ~1 month the projection of Earth's orbit changes substantially.
        assert max(values) - min(values) > 1.0

    def test_apply_light_time_correction_returns_jd(self):
        coord = star_coord(ALGOL_RA, ALGOL_DEC)
        hjd = ALGOL_M0 + ALGOL_PERIOD * 3064
        jd_geo, minutes = apply_light_time_correction(hjd, coord)
        assert jd_geo == pytest.approx(hjd + minutes / 1440.0, abs=1e-9)

    def test_seconds_per_au_constant(self):
        assert SECONDS_PER_AU == pytest.approx(499.0048, abs=0.01)


# ---------------------------------------------------------------------- #
# Ground truth from Sky & Telescope
# ---------------------------------------------------------------------- #
class TestAgainstPublishedEphemeris:
    """Independent validation of the full T = M0 + N*P -> local time chain.

    Reference values are the Algol minima published in Sky & Telescope's
    "This Week's Sky at a Glance" for 2026, quoted in EST (UTC-5):

        Friday  January  9, 2026 -- centred on 9:02 p.m. EST
        Sunday  February 1, 2026 -- centred on 7:36 p.m. EST

    The residual (~20 min) is genuine ephemeris drift: Kreiner's elements are
    anchored on observations from 2012-2020 and Algol's period changes by a few
    seconds per year, which accumulates over ~3000 cycles. What we assert is
    that we land on the *same* minimum and within that documented tolerance --
    a units or timezone bug would show up as an error of hours.
    """

    REFERENCE = [
        ("2026-01-09 21:02", 2982),
        ("2026-02-01 19:36", 2990),
    ]
    TOLERANCE_MIN = 45.0

    @pytest.mark.parametrize("est_local,expected_cycle", REFERENCE)
    def test_algol_minimum_matches_sky_and_telescope(self, est_local, expected_cycle):
        published_utc = Time(
            datetime.strptime(est_local, "%Y-%m-%d %H:%M").replace(tzinfo=ZoneInfo("EST")),
            scale="utc",
        )
        coord = star_coord(ALGOL_RA, ALGOL_DEC)
        hjd = ALGOL_M0 + ALGOL_PERIOD * expected_cycle
        predicted, _ltt = heliocentric_to_geocentric(hjd, coord)

        delta_min = abs((predicted - published_utc).to_value(u.min))
        assert delta_min < self.TOLERANCE_MIN, (
            f"cycle {expected_cycle}: predicted {predicted.utc.iso} vs published "
            f"{published_utc.utc.iso} ({delta_min:.1f} min apart)"
        )

    def test_consecutive_minima_are_one_period_apart(self):
        """In *heliocentric* time the spacing is exactly P.

        Geocentric spacing differs by the change in light-time correction
        (up to a few minutes), which is the whole point of applying it.
        """
        # float64 resolution at JD ~2.46e6 is ~5e-10 d, so this is not exact.
        assert (ALGOL_M0 + ALGOL_PERIOD * 3001) - (ALGOL_M0 + ALGOL_PERIOD * 3000) == (
            pytest.approx(ALGOL_PERIOD, abs=1e-8)
        )
        coord = star_coord(ALGOL_RA, ALGOL_DEC)
        a, ltt_a = heliocentric_to_geocentric(ALGOL_M0 + ALGOL_PERIOD * 3000, coord)
        b, ltt_b = heliocentric_to_geocentric(ALGOL_M0 + ALGOL_PERIOD * 3001, coord)
        geocentric_gap = (b - a).to_value(u.day)
        assert geocentric_gap == pytest.approx(
            ALGOL_PERIOD + (ltt_b - ltt_a) / 1440.0, abs=1e-9
        )
        assert abs(geocentric_gap - ALGOL_PERIOD) < 20.0 / 1440.0


# ---------------------------------------------------------------------- #
# Minimum search
# ---------------------------------------------------------------------- #
class TestMinimaInWindow:
    def test_finds_expected_cycle(self):
        star = _algol_star()
        start = datetime(2026, 1, 9, 14, 0, tzinfo=UTC)
        end = datetime(2026, 1, 10, 5, 0, tzinfo=UTC)
        found = minima_in_window(star, start, end, include_secondary=True)
        assert len(found) == 1
        assert found[0].kind == "primary"
        assert found[0].cycle_number == 2982

    def test_no_light_time_without_coordinates(self):
        """A star with no catalogue position cannot be light-time corrected.

        This is why the predictor resolves such stars through SIMBAD and then
        re-applies the correction -- see test_predictor.py.
        """
        star = _algol_star()
        found = minima_in_window(
            star, datetime(2026, 1, 9, 14, 0, tzinfo=UTC),
            datetime(2026, 1, 10, 5, 0, tzinfo=UTC),
        )
        assert found[0].light_time_min == 0.0

    def test_secondary_from_all_record_uses_half_period(self):
        element = ElementRecord(
            key=StarKey(name="RT", constellation="And"),
            min_type="ALL", epoch_hjd=2452500.352, period_days=0.6289287,
        )
        star = Star(key=element.key, elements=[element])
        found = minima_in_window(
            star,
            datetime(2026, 9, 20, 0, 0, tzinfo=UTC),
            datetime(2026, 9, 21, 0, 0, tzinfo=UTC),
            include_secondary=True,
        )
        kinds = {f.kind for f in found}
        assert kinds == {"primary", "secondary"}
        for f in found:
            phase = ((f.epoch_hjd - element.epoch_hjd) / element.period_days) % 1.0
            expected = 0.0 if f.kind == "primary" else 0.5
            assert phase == pytest.approx(expected, abs=1e-6)

    def test_secondary_respects_catalogue_phase(self):
        """Eccentric systems put the secondary somewhere other than 0.5."""
        element = ElementRecord(
            key=StarKey(name="XX", constellation="Cyg"),
            min_type="ALL", epoch_hjd=2452500.0, period_days=2.0, secondary_phase=0.42,
        )
        star = Star(key=element.key, elements=[element])
        found = minima_in_window(
            star, datetime(2026, 9, 20, tzinfo=UTC), datetime(2026, 9, 22, tzinfo=UTC)
        )
        secondaries = [f for f in found if f.kind == "secondary"]
        assert secondaries
        phase = ((secondaries[0].epoch_hjd - element.epoch_hjd) / element.period_days) % 1.0
        assert phase == pytest.approx(0.42, abs=1e-6)

    def test_secondary_excluded_when_disabled(self):
        element = ElementRecord(
            key=StarKey(name="RT", constellation="And"),
            min_type="ALL", epoch_hjd=2452500.352, period_days=0.6289287,
        )
        star = Star(key=element.key, elements=[element])
        found = minima_in_window(
            star, datetime(2026, 9, 20, tzinfo=UTC), datetime(2026, 9, 21, tzinfo=UTC),
            include_secondary=False,
        )
        assert all(f.kind == "primary" for f in found)

    def test_explicit_sec_record_is_used_directly(self):
        pri = ElementRecord(
            key=StarKey(name="V495", constellation="Vul"),
            min_type="PRI", epoch_hjd=2452501.2425, period_days=1.6351414,
        )
        sec = ElementRecord(
            key=pri.key, min_type="SEC", epoch_hjd=2452502.191, period_days=1.635120,
        )
        star = Star(key=pri.key, elements=[pri, sec])
        found = minima_in_window(
            star, datetime(2026, 9, 19, tzinfo=UTC), datetime(2026, 9, 22, tzinfo=UTC)
        )
        secondaries = [f for f in found if f.kind == "secondary"]
        assert secondaries
        # Must come from the SEC element, not from PRI + 0.5P.
        assert all(f.element.min_type == "SEC" for f in secondaries)

    def test_absurd_cycle_count_is_rejected(self):
        """A corrupt near-zero period must not produce millions of events."""
        element = ElementRecord(
            key=StarKey(name="QQ", constellation="Lyr"),
            min_type="ALL", epoch_hjd=2452500.0, period_days=1e-6,
        )
        star = Star(key=element.key, elements=[element])
        found = minima_in_window(
            star, datetime(2026, 9, 20, tzinfo=UTC), datetime(2026, 9, 22, tzinfo=UTC)
        )
        assert len(found) == 0

    def test_uncertainty_grows_with_cycle_count(self):
        star = _algol_star()
        near = minima_in_window(
            star, datetime(2002, 8, 13, tzinfo=UTC), datetime(2002, 8, 16, tzinfo=UTC)
        )
        far = minima_in_window(
            star, datetime(2026, 1, 9, 14, 0, tzinfo=UTC), datetime(2026, 1, 10, 5, 0, tzinfo=UTC)
        )
        assert near and far
        assert far[0].uncertainty_min > near[0].uncertainty_min
        # 24 years of period drift on Algol: tens of minutes, not seconds.
        assert far[0].uncertainty_min > 10.0


# ---------------------------------------------------------------------- #
# Geometry
# ---------------------------------------------------------------------- #
class TestGeometry:
    KAYSERI = (38.7208, 35.4875, 1050.0)

    def test_circumpolar_altitude_at_upper_culmination(self):
        """At transit, alt = 90 - |lat - dec| exactly."""
        loc = make_location(*self.KAYSERI)
        coord = star_coord(ALGOL_RA, ALGOL_DEC)
        # Walk forward until the local apparent sidereal time equals the RA.
        t = Time("2026-01-09 20:00", scale="utc")
        for _ in range(3):
            lst = t.sidereal_time("apparent", longitude=loc.lon)
            delta_hours = ((ALGOL_RA * u.deg - lst).wrap_at(180 * u.deg)).to_value(u.hourangle)
            t = t + delta_hours * u.hour
        alt = coord.transform_to(AltAz(obstime=t, location=loc)).alt.deg
        expected_max = 90.0 - abs(self.KAYSERI[0] - ALGOL_DEC)
        assert alt == pytest.approx(expected_max, abs=0.3)

    def test_lower_culmination_matches_textbook_formula(self):
        """alt_lower = dec + lat - 90.

        Algol from Kayseri: 40.96 + 38.72 - 90 = -10.3 deg, so it *does* set.
        A sign slip here would quietly mark half the sky circumpolar. Lower
        culmination is slow near the turn, so hourly sampling resolves it well.
        """
        loc = make_location(*self.KAYSERI)
        lat = self.KAYSERI[0]
        stamps = [datetime(2026, 1, 10, h, 0, tzinfo=UTC) for h in range(24)]
        out = observe_events_batch(
            [ALGOL_RA] * len(stamps), [ALGOL_DEC] * len(stamps), stamps, loc,
            half_window_h=0.0, sweep_points=1,
        )
        alts = [g.altitude_deg for g in out]
        expected_lower = ALGOL_DEC + lat - 90.0
        assert min(alts) == pytest.approx(expected_lower, abs=1.0)
        assert min(alts) < 0 < max(alts)

    def test_upper_culmination_matches_textbook_formula(self):
        """alt_upper = 90 - |lat - dec|, evaluated at the transit instant.

        Transit is a fast turn, so we locate it rather than sample hourly.
        """
        loc = make_location(*self.KAYSERI)
        lat = self.KAYSERI[0]
        coord = star_coord(ALGOL_RA, ALGOL_DEC)
        t = Time("2026-01-10 15:00", scale="utc")
        for _ in range(3):
            lst = t.sidereal_time("apparent", longitude=loc.lon)
            delta_h = ((ALGOL_RA * u.deg - lst).wrap_at(180 * u.deg)).to_value(u.hourangle)
            t = t + delta_h * u.hour
        alt = coord.transform_to(AltAz(obstime=t, location=loc)).alt.deg
        expected_upper = 90.0 - abs(lat - ALGOL_DEC)
        assert alt == pytest.approx(expected_upper, abs=0.3)

    def test_genuinely_circumpolar_star_never_sets(self):
        """Kochab (dec +74.2) from Kayseri: circumpolar limit is dec > +51.3."""
        loc = make_location(*self.KAYSERI)
        lat = self.KAYSERI[0]
        ra, dec = 222.6750, 74.1555
        assert dec > 90.0 - lat, "test star must actually be circumpolar"
        stamps = [datetime(2026, 1, 10, h, 0, tzinfo=UTC) for h in range(24)]
        out = observe_events_batch(
            [ra] * len(stamps), [dec] * len(stamps), stamps, loc,
            half_window_h=0.0, sweep_points=1,
        )
        alts = [g.altitude_deg for g in out]
        expected_lower = dec + lat - 90.0
        assert expected_lower > 0
        assert min(alts) == pytest.approx(expected_lower, abs=1.0)
        assert all(a > 0 for a in alts)

    def test_southern_star_never_rises(self):
        """Acrux (dec -63) is permanently below the horizon from Kayseri."""
        loc = make_location(*self.KAYSERI)
        ra, dec = 186.6450, -63.0991  # Acrux, 12h26m
        stamps = [datetime(2026, 1, 10, h, 0, tzinfo=UTC) for h in range(24)]
        out = observe_events_batch(
            [ra] * len(stamps), [dec] * len(stamps), stamps, loc,
            half_window_h=0.0, sweep_points=1,
        )
        assert max(g.altitude_deg for g in out) < 0
        assert is_declination_plausible(dec, self.KAYSERI[0], 30.0) is False

    def test_moon_illumination_bounds(self):
        for iso in ("2026-01-01 00:00", "2026-02-01 00:00", "2026-09-20 20:00"):
            frac, elong = moon_illumination(Time(iso, scale="utc"))
            assert 0.0 <= frac <= 1.0
            assert 0.0 <= elong <= 180.0

    def test_phase_names(self):
        assert phase_name(0.0, True) == "New Moon"
        assert phase_name(1.0, True) == "Full Moon"
        assert phase_name(0.5, True) == "First Quarter"
        assert phase_name(0.5, False) == "Last Quarter"
        assert phase_name(0.2, True) == "Waxing Crescent"
        assert phase_name(0.8, False) == "Waning Gibbous"


# ---------------------------------------------------------------------- #
# Night window
# ---------------------------------------------------------------------- #
class TestNightWindow:
    def test_window_spans_evening_to_morning(self):
        tz = ZoneInfo("Europe/Istanbul")
        loc = make_location(38.7208, 35.4875, 1050)
        w = night_window(date(2026, 1, 9), loc, tz)
        assert w.start_utc == datetime(2026, 1, 9, 14, 0, tzinfo=UTC)   # 17:00 +03
        assert w.end_utc == datetime(2026, 1, 10, 5, 0, tzinfo=UTC)     # 08:00 +03

    def test_solar_events_are_in_the_window(self):
        tz = ZoneInfo("Europe/Istanbul")
        loc = make_location(38.7208, 35.4875, 1050)
        w = night_window(date(2026, 1, 9), loc, tz)
        assert w.sunset_utc is not None and w.sunrise_utc is not None
        assert w.start_utc <= w.sunset_utc <= w.end_utc
        assert w.start_utc <= w.sunrise_utc <= w.end_utc
        assert w.sunset_utc < w.sunrise_utc
        # Astronomical darkness is a subset of night.
        assert w.sunset_utc < w.dark_start_utc < w.dark_end_utc < w.sunrise_utc

    def test_sunset_is_plausible_for_kayseri_in_january(self):
        """Kayseri sunset on 9 Jan is ~17:34 local (14:34 UTC)."""
        tz = ZoneInfo("Europe/Istanbul")
        loc = make_location(38.7208, 35.4875, 1050)
        w = night_window(date(2026, 1, 9), loc, tz)
        local_sunset = w.sunset_utc.astimezone(tz)
        assert local_sunset.hour == 17
        assert 25 <= local_sunset.minute <= 45


# ---------------------------------------------------------------------- #
# Timezone handling
# ---------------------------------------------------------------------- #
class TestTimezone:
    def test_explicit_timezone_wins(self):
        assert str(timezone_from_location(38.7, 35.4, "Europe/Istanbul")) == "Europe/Istanbul"

    def test_invalid_timezone_falls_back(self):
        tz = timezone_from_location(38.7, 35.4, "Mars/Olympus")
        assert tz is not None

    def test_longitude_fallback_is_sane(self):
        # Kayseri (35.5 E) -> UTC+2 or +3; Greenwich -> UTC; Tokyo (139.7 E) -> +9
        assert timezone_from_location(38.7, 35.4, None).utcoffset(
            datetime(2026, 1, 1, tzinfo=UTC)
        ).total_seconds() / 3600 == 2.0
        assert timezone_from_location(51.5, 0.0, None).utcoffset(
            datetime(2026, 1, 1, tzinfo=UTC)
        ).total_seconds() / 3600 == 0.0
        assert timezone_from_location(35.7, 139.7, None).utcoffset(
            datetime(2026, 1, 1, tzinfo=UTC)
        ).total_seconds() / 3600 == 9.0


# ---------------------------------------------------------------------- #
# Scoring / duration
# ---------------------------------------------------------------------- #
class TestScoring:
    def test_zenith_dark_deep_event_beats_low_moonlit_shallow(self):
        good, good_q = score_event(85, 88, 150, 0.0, 5.0, 1.5, -40)
        bad, bad_q = score_event(31, 32, 31, 100.0, 9.4, 0.3, -7)
        assert good > bad
        assert good_q == "excellent"
        assert bad_q in ("fair", "good")

    def test_score_is_bounded(self):
        for args in [
            (90, 90, 180, 0, 1.0, 3.0, -60),
            (30, 30, 30, 100, 15.0, 0.0, 10),
            (0, 0, 0, 100, None, None, 0),
        ]:
            score, _ = score_event(*args)
            assert 0.0 <= score <= 100.0

    def test_unknown_photometry_does_not_crash(self):
        score, quality = score_event(60, 65, 80, 30, None, None, -12)
        assert score > 0
        assert quality in ("fair", "good", "excellent")

    @pytest.mark.parametrize(
        "vt,expected",
        [("EA", 1.5), ("EA/SD", 1.5), ("EB", 2.5), ("EW", 3.0), ("EW/KW", 3.0), ("ELL", 6.0)],
    )
    def test_duration_model(self, vt, expected):
        assert estimate_duration_h(vt, 3.0) == expected

    def test_duration_fallback_for_unknown_type(self):
        d = estimate_duration_h("ZZZ", 2.0)
        assert 0.5 <= d <= 6.0
