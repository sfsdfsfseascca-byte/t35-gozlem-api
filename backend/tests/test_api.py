"""API integration tests using FastAPI TestClient (offline)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.cache import InMemoryCache, set_cache
from app.datasource import set_ephemeris_source
from app.main import app
from app.predictor import EclipsePredictor

from .helpers import StubSimbadClient, SIMBAD_TABLE


@pytest.fixture
def client(offline_source):
    # Wire predictor with stub SIMBAD
    stub = StubSimbadClient(table=SIMBAD_TABLE)
    predictor = EclipsePredictor(source=offline_source, simbad=stub)
    # Override dependency
    app.dependency_overrides = {}
    from app.main import predictor_dependency

    def _override():
        return predictor

    app.dependency_overrides[predictor_dependency] = _override

    set_cache(InMemoryCache(max_items=1000))
    set_ephemeris_source(offline_source)

    with TestClient(app) as c:
        yield c

    app.dependency_overrides.clear()
    set_ephemeris_source(None)


class TestHealth:
    def test_health_ok(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        j = r.json()
        assert j["status"] in ("ok", "degraded")
        assert j["ephemeris_loaded"] is True
        assert j["ephemeris_stars"] > 0

    def test_root(self, client):
        r = client.get("/")
        assert r.status_code == 200
        assert "service" in r.json()


class TestNightEndpoint:
    def test_post_night(self, client):
        body = {
            "date": "2026-09-20",
            "location": {"latitude": 38.7208, "longitude": 35.4875, "timezone": "Europe/Istanbul"},
            "min_altitude_deg": 30,
            "max_vmag": 9.5,
            "max_results": 10,
        }
        r = client.post("/api/v1/night", json=body)
        assert r.status_code == 200, r.text
        j = r.json()
        assert "events" in j
        assert "window" in j
        assert "moon" in j
        if j["events"]:
            e = j["events"][0]
            assert "star_name" in e
            assert "time_local" in e
            assert "v_magnitude" in e
            assert "peak_altitude_deg" in e
            assert "moon_separation_deg" in e

    def test_get_night(self, client):
        r = client.get("/api/v1/night?date=2026-09-20&lat=38.7208&lon=35.4875&timezone=Europe/Istanbul&max_results=5")
        assert r.status_code == 200
        assert len(r.json()["events"]) <= 5

    def test_tonight(self, client):
        r = client.get("/api/v1/tonight?lat=38.7208&lon=35.4875&timezone=Europe/Istanbul&max_results=5")
        assert r.status_code == 200

    def test_defaults(self, client):
        r = client.get("/api/v1/defaults")
        assert r.status_code == 200
        assert "min_altitude_deg" in r.json()

    def test_catalog_stats(self, client):
        r = client.get("/api/v1/catalog/stats?limit=3")
        assert r.status_code == 200
        assert r.json()["unique_stars"] > 0

    def test_simbad_resolve(self, client):
        r = client.get("/api/v1/simbad/RZ%20Cas")
        assert r.status_code == 200
        j = r.json()
        assert j["identifier"] == "RZ Cas"

    def test_cache_hit(self, client):
        body = {
            "date": "2026-09-20",
            "location": {"latitude": 38.7208, "longitude": 35.4875, "timezone": "Europe/Istanbul"},
            "max_results": 5,
        }
        r1 = client.post("/api/v1/night", json=body)
        r2 = client.post("/api/v1/night", json=body)
        assert r1.status_code == 200 and r2.status_code == 200
        # Second should be cached (elapsedMs small)
        assert r2.json()["stats"]["elapsed_ms"] <= r1.json()["stats"]["elapsed_ms"]
