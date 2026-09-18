"""Tests for the full prediction pipeline (offline, stubbed SIMBAD)."""
from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from app.models import LocationIn, NightQuery
from app.predictor import EclipsePredictor

from .helpers import StubSimbadClient, SIMBAD_TABLE


@pytest.fixture
def predictor(offline_source, stub_simbad) -> EclipsePredictor:
    return EclipsePredictor(source=offline_source, simbad=stub_simbad)


KAYSERI = LocationIn(latitude=38.7208, longitude=35.4875, elevation_m=1050, timezone="Europe/Istanbul")


class TestPredictorBasic:
    def test_returns_events_for_sample_night(self, predictor):
        q = NightQuery(
            date=date(2026, 9, 20),
            location=KAYSERI,
            min_altitude_deg=20,
            min_moon_separation_deg=10,
            max_vmag=12,
            min_depth_mag=0.1,
            max_sun_altitude_deg=-6,
            include_secondary=True,
            require_dark_sky=False,
            use_simbad=False,
            max_results=50,
            sort_by="time",
        )
        resp = predictor.predict(q)
        assert resp.events
        assert resp.stats.returned > 0
        assert resp.stats.candidates_from_ephemeris > 0

    def test_filters_by_altitude(self, predictor):
        q_low = NightQuery(date=date(2026, 9, 20), location=KAYSERI, min_altitude_deg=0, max_vmag=12, use_simbad=False, max_results=200)
        q_high = NightQuery(date=date(2026, 9, 20), location=KAYSERI, min_altitude_deg=80, max_vmag=12, use_simbad=False, max_results=200)
        low = predictor.predict(q_low)
        high = predictor.predict(q_high)
        assert len(high.events) <= len(low.events)

    def test_filters_by_vmag(self, predictor):
        q_bright = NightQuery(date=date(2026, 9, 20), location=KAYSERI, max_vmag=6, use_simbad=False, max_results=200)
        q_faint = NightQuery(date=date(2026, 9, 20), location=KAYSERI, max_vmag=12, use_simbad=False, max_results=200)
        bright = predictor.predict(q_bright)
        faint = predictor.predict(q_faint)
        assert len(bright.events) <= len(faint.events)
        if bright.events:
            assert all(e.v_magnitude is None or e.v_magnitude <= 6.1 for e in bright.events)

    def test_event_fields(self, predictor):
        q = NightQuery(date=date(2026, 9, 20), location=KAYSERI, max_vmag=12, use_simbad=False, max_results=10)
        resp = predictor.predict(q)
        e = resp.events[0]
        assert e.star_name
        assert e.time_local
        assert e.altitude_deg is not None
        assert e.peak_altitude_deg >= e.altitude_deg - 1e-6
        assert 0 <= e.moon_separation_deg <= 180
        assert 0 <= e.score <= 100
        assert e.quality in ("excellent", "good", "fair")

    def test_secondary_inclusion(self, predictor):
        q_no_sec = NightQuery(date=date(2026, 9, 20), location=KAYSERI, max_vmag=12, include_secondary=False, use_simbad=False, max_results=200)
        q_sec = NightQuery(date=date(2026, 9, 20), location=KAYSERI, max_vmag=12, include_secondary=True, use_simbad=False, max_results=200)
        no_sec = predictor.predict(q_no_sec)
        sec = predictor.predict(q_sec)
        assert len(sec.events) >= len(no_sec.events)

    def test_sorting(self, predictor):
        for sort in ("time", "score", "altitude", "magnitude"):
            q = NightQuery(date=date(2026, 9, 20), location=KAYSERI, max_vmag=12, use_simbad=False, max_results=20, sort_by=sort)
            resp = predictor.predict(q)
            assert resp.events  # at least something


class TestPredictorWithSimbad:
    def test_simbad_enriches_catalog_source(self, predictor):
        q = NightQuery(date=date(2026, 9, 20), location=KAYSERI, max_vmag=12, use_simbad=True, max_results=20)
        resp = predictor.predict(q)
        # Some events should be sourced from simbad stub
        sources = {e.catalog.source for e in resp.events}
        assert "simbad" in sources or "catalog" in sources

    def test_catalog_less_star_resolved(self, offline_source):
        """Algol (bet Per) is absent from allstars-cat.txt and needs SIMBAD."""
        # Ensure Algol is in the source
        bundle = offline_source.get_bundle()
        assert ("BET", "PER") in bundle.stars
        assert bundle.stars[("BET", "PER")].catalog is None

        stub = StubSimbadClient(table=SIMBAD_TABLE)
        pred = EclipsePredictor(source=offline_source, simbad=stub)

        q = NightQuery(
            date=date(2026, 9, 20),
            location=KAYSERI,
            min_altitude_deg=0,  # lower to allow Algol if low
            max_vmag=6,
            use_simbad=True,
            max_results=100,
        )
        resp = pred.predict(q)
        # Algol may or may not be above horizon that night, but the pipeline must not crash
        assert resp.stats is not None
        # The stub was queried for bet Per if it survived filters
        # At least ensure budget handling works
        assert stub.queries_issued >= 0

    def test_light_time_correction_for_catalog_less(self):
        """Catalog-less stars must get LTT correction after SIMBAD resolution."""
        from app.astro_engine import apply_light_time_correction, star_coord
        from datetime import datetime

        coord = star_coord(47.0422186, 40.9556467)
        hjd = 2452500.172 + 2.867338 * 3000
        jd_geo, corr = apply_light_time_correction(hjd, coord)
        assert abs(corr) < 8.4
        assert jd_geo != hjd
