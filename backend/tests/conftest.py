"""Shared pytest fixtures.

Everything is offline by default: the real Krakow files are replaced by the
small samples in ``backend/data/samples`` and SIMBAD is stubbed. Tests that need
the network live in ``test_integration_live.py`` and are skipped unless
``EH_RUN_LIVE_TESTS=1``.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict

import pytest

# Force the in-memory cache and disable live SIMBAD for the whole session
# before any app module reads the environment.
os.environ.setdefault("EH_CACHE_BACKEND", "memory")
os.environ.setdefault("EH_SIMBAD_ENABLED", "false")
os.environ.setdefault("EH_EPHEM_LOCAL_DIR", str(Path(__file__).resolve().parent / "_unused"))

from app.cache import InMemoryCache, set_cache  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.datasource import EphemerisBundle, EphemerisSource, set_ephemeris_source  # noqa: E402
from app.ephemeris import (  # noqa: E402
    Star,
    build_stars,
    catalog_fingerprint,
    parse_allstars_cat,
    parse_ephem_txt,
)

from .helpers import SIMBAD_TABLE, StubSimbadClient  # noqa: E402

SAMPLES = Path(__file__).resolve().parent.parent / "data" / "samples"


@pytest.fixture(autouse=True)
def _fresh_cache():
    """Every test gets an empty in-memory cache and reset singletons."""
    set_cache(InMemoryCache(max_items=1000))
    yield
    set_cache(InMemoryCache(max_items=100))
    set_ephemeris_source(None)


@pytest.fixture
def ephem_text() -> str:
    return (SAMPLES / "EPHEM_sample.txt").read_text(encoding="latin-1")


@pytest.fixture
def catalog_text() -> str:
    return (SAMPLES / "allstars_sample.txt").read_text(encoding="utf-8")


@pytest.fixture
def elements(ephem_text):
    return parse_ephem_txt(ephem_text)


@pytest.fixture
def catalog(catalog_text):
    return parse_allstars_cat(catalog_text)


@pytest.fixture
def stars(elements, catalog) -> Dict[tuple, Star]:
    return build_stars(elements, catalog)


@pytest.fixture
def stub_simbad() -> StubSimbadClient:
    return StubSimbadClient(table=SIMBAD_TABLE)


@pytest.fixture
def offline_source(stars, ephem_text):
    """An EphemerisSource pre-seeded with the sample bundle (no network)."""
    source = EphemerisSource(settings=get_settings())
    source._bundle = EphemerisBundle(
        stars=stars,
        content_hash=catalog_fingerprint(ephem_text),
        downloaded_at=datetime.now(timezone.utc),
        source_url="file://samples",
        element_records=sum(len(s.elements) for s in stars.values()),
        catalog_records=len([s for s in stars.values() if s.catalog]),
        from_cache=True,
    )
    set_ephemeris_source(source)
    return source
