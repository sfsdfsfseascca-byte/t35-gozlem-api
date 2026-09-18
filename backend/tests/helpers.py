"""Reusable test doubles and reference data.

Kept separate from ``conftest.py`` so test modules can import it normally
(``from tests.helpers import StubSimbadClient``).
"""
from __future__ import annotations

from typing import Dict, List, Optional

from app.cache import InMemoryCache
from app.config import Settings
from app.simbad import SimbadClient

# Coordinates cross-checked against SIMBAD on 2026-09-18.
SIMBAD_TABLE: Dict[str, Dict] = {
    # Algol. Absent from allstars-cat.txt -> exercises the SIMBAD-only path.
    "bet Per": {
        "main_id": "* bet Per", "ra": 47.04221855625, "dec": 40.95564667027778,
        "V": 2.12, "otype": "SB*", "sp_type": "B8V",
    },
    "beta Per": {
        "main_id": "* bet Per", "ra": 47.04221855625, "dec": 40.95564667027778,
        "V": 2.12, "otype": "SB*", "sp_type": "B8V",
    },
    "bet Lyr": {
        "main_id": "V* bet Lyr", "ra": 279.2114, "dec": 33.3627,
        "V": 3.52, "otype": "EB*", "sp_type": "B8.5II",
    },
    "bet Aur": {
        "main_id": "* bet Aur", "ra": 89.8817, "dec": 44.2009,
        "V": 1.90, "otype": "SB*", "sp_type": "K0IV",
    },
    "RZ Cas": {
        "main_id": "V* RZ Cas", "ra": 42.23129265439, "dec": 69.63428930629,
        "V": 6.26, "otype": "SB*", "sp_type": "A3V",
    },
    "lam Tau": {
        "main_id": "* lam Tau", "ra": 60.1700698910475, "dec": 12.490344441007225,
        "V": 3.41, "otype": "SB*", "sp_type": "B4IV",
    },
    "b Per": {
        "main_id": "* b Per", "ra": 64.56092274545583, "dec": 50.295493065878055,
        "V": 4.594, "otype": "SB*", "sp_type": "A1III",
    },
}


class StubSimbadClient(SimbadClient):
    """SIMBAD stand-in with a canned object table and a call log.

    Mirrors the real client's public surface (``resolve`` / ``resolve_many`` /
    ``cache_get`` / ``cache_put`` / ``queries_issued`` / ``reset_budget``) so
    the predictor cannot tell the difference -- which is the point: tests then
    exercise the real prediction code path with zero network traffic.
    """

    def __init__(
        self,
        table: Optional[Dict[str, Dict]] = None,
        fail: Optional[List[str]] = None,
        cache: Optional[InMemoryCache] = None,
        settings: Optional[Settings] = None,
    ) -> None:
        # Use a fresh Settings instance so tests that mutate max_queries do not
        # pollute the global lru_cache singleton.
        isolated = settings or Settings()
        isolated.cache_backend = "memory"
        isolated.simbad_enabled = True
        super().__init__(cache=cache or InMemoryCache(max_items=1000), settings=isolated)
        self.table = dict(table if table is not None else SIMBAD_TABLE)
        self.fail = set(fail or [])
        self.calls: List[str] = []
        # The real client refuses to query when astroquery is unavailable; the
        # stub overrides the query method so it must also look configured.
        self._configured = True
        self._config_failed = False

    def _query_simbad(self, identifier):  # noqa: D102 - deliberate override
        # Mimic the real client's budget consumption.
        if not self._allow_query():
            return None
        self.calls.append(identifier)
        if identifier in self.fail:
            return None
        row = self.table.get(identifier)
        return dict(row) if row else None

    def _query_vizier_gcvs(self, identifier):  # noqa: D102 - deliberate override
        if not self._allow_query():
            return None
        return None
