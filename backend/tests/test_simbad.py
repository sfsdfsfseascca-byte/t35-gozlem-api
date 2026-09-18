"""Tests for the SIMBAD resolver, its cache discipline and its query budget.

The negative-caching and budget tests matter more than they look: getting them
wrong is how a deployed server gets its IP blocked by CDS.
"""
from __future__ import annotations

import pytest

from app.cache import InMemoryCache, simbad_key
from app.config import get_settings
from app.simbad import (
    SimbadClient,
    SimbadResult,
    _crossmatch_arcsec,
    _sexagesimal_deg_to_deg,
    _sexagesimal_hours_to_deg,
)

from tests.helpers import SIMBAD_TABLE, StubSimbadClient


@pytest.fixture
def client(stub_simbad) -> StubSimbadClient:
    return stub_simbad


class TestResolution:
    def test_resolves_known_star(self, client):
        result = client.resolve(["RZ Cas"])
        assert result.found
        assert result.source == "simbad"
        assert result.ra_deg == pytest.approx(42.23129265439, abs=1e-6)
        assert result.dec_deg == pytest.approx(69.63428930629, abs=1e-6)
        assert result.v_mag == pytest.approx(6.26)
        assert result.main_id == "V* RZ Cas"

    def test_falls_through_to_next_identifier(self, client):
        result = client.resolve(["NOT A STAR", "RZ Cas"])
        assert result.found
        assert result.tried[0] == "NOT A STAR"
        assert "RZ Cas" in result.tried

    def test_unresolvable_is_negative(self, client):
        result = client.resolve(["ZZZ Notreal"])
        assert not result.found
        assert result.negative is True
        assert result.budget_exhausted is False

    def test_catalogue_fallback_when_simbad_fails(self, client):
        result = client.resolve(["ZZZ Notreal"], catalog_ra_deg=10.0, catalog_dec_deg=20.0)
        assert result.found
        assert result.source == "catalog"
        assert result.ra_deg == 10.0
        assert "SIMBAD unresolved" in (result.reason or "")


class TestCaching:
    def test_positive_result_is_cached(self, client):
        client.resolve(["RZ Cas"])
        cached = client.cache_get("RZ Cas")
        assert cached is not None and cached.found

    def test_second_call_makes_no_query(self, client):
        client.resolve(["RZ Cas"])
        before = len(client.calls)
        client.resolve(["RZ Cas"])
        assert len(client.calls) == before

    def test_negative_result_is_cached(self):
        """The single most important anti-ban behaviour."""
        client = StubSimbadClient(table=SIMBAD_TABLE)
        client._configured = True
        client.settings.simbad_enabled = True
        client.resolve(["ZZZ Notreal"])
        cached = client.cache_get("ZZZ Notreal")
        assert cached is not None
        assert cached.negative is True
        calls_after_first = len(client.calls)
        client.resolve(["ZZZ Notreal"])
        assert len(client.calls) == calls_after_first, "negative cache must prevent re-querying"

    def test_negative_cache_has_shorter_ttl(self, client):
        settings = client.settings
        assert settings.simbad_negative_ttl < settings.simbad_ttl

    def test_force_refresh_bypasses_cache(self, client):
        client.resolve(["RZ Cas"])
        before = len(client.calls)
        client.resolve(["RZ Cas"], force_refresh=True)
        assert len(client.calls) > before

    def test_cache_key_is_case_insensitive(self):
        assert simbad_key("RZ Cas") == simbad_key("rz cas")

    def test_cache_roundtrip_preserves_fields(self, client):
        original = client.resolve(["RZ Cas"])
        restored = SimbadResult.from_cache_dict(original.to_cache_dict())
        assert restored.ra_deg == original.ra_deg
        assert restored.dec_deg == original.dec_deg
        assert restored.v_mag == original.v_mag
        assert restored.main_id == original.main_id
        assert restored.source == original.source


class TestQueryBudget:
    def test_budget_exhaustion_is_not_negatively_cached(self):
        """A budget-limited give-up is not a fact about the sky."""
        client = StubSimbadClient(table=SIMBAD_TABLE)
        client._configured = True
        client.settings.simbad_enabled = True
        client.settings.simbad_max_queries_per_request = 0

        result = client.resolve(["RZ Cas"])
        assert not result.found
        assert result.budget_exhausted is True
        assert result.negative is False
        assert client.cache_get("RZ Cas") is None, "must NOT poison the cache"

    def test_budget_is_consumed_and_resettable(self):
        client = StubSimbadClient(table=SIMBAD_TABLE)
        client._configured = True
        client.settings.simbad_enabled = True
        client.settings.simbad_max_queries_per_request = 2
        client.resolve(["aaa"])
        client.resolve(["bbb"])
        assert client.queries_issued == 2
        assert not client._budget_available()
        client.reset_budget()
        assert client.queries_issued == 0
        assert client._budget_available()


class TestResolveMany:
    def test_resolves_a_batch(self, client):
        jobs = [
            {"star_id": "a", "candidates": ["RZ Cas"]},
            {"star_id": "b", "candidates": ["bet Per"]},
            {"star_id": "c", "candidates": ["lam Tau"]},
        ]
        out = client.resolve_many(jobs)
        assert set(out) == {"a", "b", "c"}
        assert all(r.found for r in out.values())

    def test_cache_hits_do_not_consume_budget(self, client):
        client.resolve(["RZ Cas"])
        client.settings.simbad_max_queries_per_request = 0
        out = client.resolve_many([{"star_id": "a", "candidates": ["RZ Cas"]}])
        assert out["a"].found

    def test_results_keyed_by_star_id(self, client):
        out = client.resolve_many([{"star_id": ("BET", "PER"), "candidates": ["bet Per"]}])
        assert ("BET", "PER") in out


class TestCrossMatchGuard:
    def test_far_match_is_rejected(self):
        """SIMBAD resolving 'mu Aqr' 7.6 deg away must not be trusted."""
        client = StubSimbadClient(
            table={"TRAP STAR": {"main_id": "wrong", "ra": 10.0, "dec": 10.0, "V": 8.0}}
        )
        client._configured = True
        client.settings.simbad_enabled = True
        result = client.resolve(["TRAP STAR"], catalog_ra_deg=10.0, catalog_dec_deg=20.0)
        # Rejected as a SIMBAD match, so it falls through to catalogue coords.
        assert result.source == "catalog"
        assert result.dec_deg == 20.0

    def test_close_match_is_accepted(self, client):
        result = client.resolve(["RZ Cas"], catalog_ra_deg=42.2313, catalog_dec_deg=69.6343)
        assert result.source == "simbad"
        assert result.crossmatch_arcsec is not None
        assert result.crossmatch_arcsec < 10.0


class TestHelpers:
    @pytest.mark.parametrize(
        "text,expected",
        [("03 08 10.13", 47.0422), ("2 48 55.5", 42.23125), ("23 11 10.1", 347.79208)],
    )
    def test_sexagesimal_hours(self, text, expected):
        assert _sexagesimal_hours_to_deg(text) == pytest.approx(expected, abs=1e-3)

    @pytest.mark.parametrize(
        "text,expected",
        [("+40 57 20.4", 40.95567), ("-16 23 42.9", -16.39525), ("+69 38 3.4", 69.63428)],
    )
    def test_sexagesimal_degrees(self, text, expected):
        assert _sexagesimal_deg_to_deg(text) == pytest.approx(expected, abs=1e-4)

    def test_crossmatch_arcsec_zero_for_identical(self):
        assert _crossmatch_arcsec(10.0, 20.0, 10.0, 20.0) == pytest.approx(0.0, abs=1e-6)

    def test_crossmatch_arcsec_one_degree(self):
        assert _crossmatch_arcsec(10.0, 20.0, 11.0, 20.0) == pytest.approx(
            3600 * 0.9397, abs=20
        )

    def test_crossmatch_none_when_missing(self):
        assert _crossmatch_arcsec(10.0, 20.0, None, 20.0) is None
