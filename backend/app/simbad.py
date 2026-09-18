"""
SIMBAD resolution with disciplined caching.

Why this module is paranoid
---------------------------
CDS/SIMBAD is a free academic service. A naive implementation that fires one
``query_object`` per candidate per request will:

  1. take 30-60 s per API call (SIMBAD round-trips are ~0.2-1.5 s each),
  2. hammer CDS from a single cloud IP, which is exactly the pattern their
     abuse detection looks for,
  3. get that IP throttled or blocked, breaking the app for every user.

So we apply, in order:

  * **Zero-query pre-filter.** ``allstars-cat.txt`` already contains J2000
    coordinates and V magnitudes for ~92% of the catalogue. We filter on
    brightness/altitude using those first, so SIMBAD is only ever asked about
    stars that genuinely survived.
  * **Long-lived positive cache** (45 days). Stellar coordinates do not change.
  * **Negative cache** (12 h). "SIMBAD has no object called X" is also a stable
    answer and re-asking it every request is the #1 ban vector.
  * **Concurrency cap + retries with backoff** rather than a burst.
  * **Per-request query budget** (``simbad_max_queries_per_request``). Beyond it
    we degrade to catalogue coordinates instead of failing or flooding CDS.
  * **Cross-match guard.** If SIMBAD's position disagrees with the catalogue
    position by more than ``simbad_max_crossmatch_arcsec`` we reject the match:
    short identifiers such as "R CMa" can otherwise resolve to an unrelated
    object and silently corrupt the prediction.

Fallback ladder for one star::

    cache hit ──► done
    SIMBAD by name variants (≤6 tries, 1 request each) ──► cache + done
    VizieR GCVS lookup by name ──► cache + done
    allstars-cat.txt coordinates ──► cache + done  (source="catalog")
    give up ──► negative cache
"""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .cache import BaseCache, get_cache, simbad_key
from .config import Settings, get_settings

log = logging.getLogger("eclipse_hunter.simbad")

VIZIER_FIELDS = [
    "main_id", "ra", "dec", "coo_err_maj", "coo_err_min", "coo_wavelength",
    "V", "B", "plx_value", "otype", "sp_type",
]


@dataclass
class SimbadResult:
    """Normalised resolution outcome for one star."""

    identifier: str
    found: bool
    ra_deg: Optional[float] = None
    dec_deg: Optional[float] = None
    v_mag: Optional[float] = None
    main_id: Optional[str] = None
    object_type: Optional[str] = None
    spectral_type: Optional[str] = None
    source: str = "none"          # "simbad" | "vizier" | "catalog" | "none"
    negative: bool = False
    #: True when we gave up because the per-request query budget ran out rather
    #: than because SIMBAD said "no such object". MUST NOT be negatively cached,
    #: otherwise a busy server would permanently poison the cache with
    #: false "not found" entries for stars that resolve perfectly well.
    budget_exhausted: bool = False
    reason: Optional[str] = None
    tried: List[str] = field(default_factory=list)
    crossmatch_arcsec: Optional[float] = None
    elapsed_ms: int = 0

    def to_cache_dict(self) -> Dict[str, Any]:
        return {
            "identifier": self.identifier,
            "found": self.found,
            "ra_deg": self.ra_deg,
            "dec_deg": self.dec_deg,
            "v_mag": self.v_mag,
            "main_id": self.main_id,
            "object_type": self.object_type,
            "spectral_type": self.spectral_type,
            "source": self.source,
            "negative": self.negative,
            "budget_exhausted": self.budget_exhausted,
            "reason": self.reason,
            "tried": self.tried,
        }

    @classmethod
    def from_cache_dict(cls, data: Dict[str, Any]) -> "SimbadResult":
        return cls(
            identifier=data.get("identifier", ""),
            found=bool(data.get("found")),
            ra_deg=data.get("ra_deg"),
            dec_deg=data.get("dec_deg"),
            v_mag=data.get("v_mag"),
            main_id=data.get("main_id"),
            object_type=data.get("object_type"),
            spectral_type=data.get("spectral_type"),
            source=data.get("source", "none"),
            negative=bool(data.get("negative")),
            budget_exhausted=bool(data.get("budget_exhausted")),
            reason=data.get("reason"),
            tried=list(data.get("tried") or []),
        )


class SimbadClient:
    """Thin, cached wrapper around ``astroquery.simbad`` / ``astroquery.vizier``.

    Thread-safe. All astroquery configuration happens in ``__init__`` so worker
    threads never mutate shared class state concurrently.
    """

    def __init__(
        self,
        cache: Optional[BaseCache] = None,
        settings: Optional[Settings] = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.cache = cache or get_cache()
        self._local = threading.local()
        self._configured = False
        self._config_failed = False
        self._simbad = None
        self._config_lock = threading.Lock()
        self._query_count = 0
        self._count_lock = threading.Lock()

    # ------------------------------------------------------------------ #
    # astroquery setup
    # ------------------------------------------------------------------ #
    def _configure(self) -> bool:
        """Import and configure astroquery exactly once per process.

        Two failure modes are guarded here:

        * **Concurrent import.** ``resolve_many`` runs a thread pool, and
          ``import astroquery.simbad`` is *not* safe to execute from several
          threads at once (it mutates a shared astropy ``ConfigLogger`` and can
          raise ``'Logger' object has no attribute '_set_defaults'``). The lock
          below serialises it.
        * **Retry storms.** If configuration genuinely fails we latch the
          failure instead of letting every subsequent call re-enter the lock and
          re-attempt the import -- which would serialise the whole pool.
        """
        if self._configured:
            return True
        if self._config_failed:
            return False
        with self._config_lock:
            if self._configured:
                return True
            if self._config_failed:
                return False
            try:
                from astroquery.simbad import Simbad

                Simbad.TIMEOUT = self.settings.simbad_timeout
                # Request exactly the columns we need -- fewer columns means a
                # smaller response and less load on CDS.
                for field_name in VIZIER_FIELDS:
                    try:
                        Simbad.add_votable_fields(field_name)
                    except Exception:
                        # Already present, or not a valid field in this version.
                        pass
                self._simbad = Simbad
                self._configured = True
                log.info("astroquery/SIMBAD configured (timeout %.0fs, %d fields)",
                         self.settings.simbad_timeout, len(VIZIER_FIELDS))
                return True
            except Exception as exc:
                self._config_failed = True
                log.error(
                    "astroquery unavailable (%s: %s); falling back to catalogue "
                    "coordinates only. SIMBAD verification is disabled for this process.",
                    type(exc).__name__, exc,
                )
                return False

    def preconfigure(self) -> bool:
        """Eagerly configure astroquery on the main thread.

        Call this during application startup so the first user request never
        pays the import cost (or races with itself)."""
        return self._configure()

    # ------------------------------------------------------------------ #
    # Budget accounting
    # ------------------------------------------------------------------ #
    def _budget_available(self) -> bool:
        """Peek at the remaining per-request budget without consuming it."""
        with self._count_lock:
            return self._query_count < self.settings.simbad_max_queries_per_request

    def _allow_query(self) -> bool:
        with self._count_lock:
            if self._query_count >= self.settings.simbad_max_queries_per_request:
                return False
            self._query_count += 1
            return True

    def reset_budget(self) -> None:
        with self._count_lock:
            self._query_count = 0

    @property
    def queries_issued(self) -> int:
        return self._query_count

    # ------------------------------------------------------------------ #
    # Cache
    # ------------------------------------------------------------------ #
    def cache_get(self, identifier: str) -> Optional[SimbadResult]:
        data = self.cache.get_json(simbad_key(identifier))
        if data is None:
            return None
        try:
            return SimbadResult.from_cache_dict(data)
        except Exception:  # pragma: no cover - corrupt cache entry
            log.warning("Corrupt SIMBAD cache entry for %s; ignoring", identifier)
            return None

    def cache_put(self, result: SimbadResult) -> None:
        ttl = self.settings.simbad_ttl if result.found else self.settings.simbad_negative_ttl
        ttl = BaseCache.jittered(ttl, self.settings.ttl_jitter)
        self.cache.set_json(simbad_key(result.identifier), result.to_cache_dict(), ttl)

    # ------------------------------------------------------------------ #
    # Live queries
    # ------------------------------------------------------------------ #
    def _query_simbad(self, identifier: str) -> Optional[Dict[str, Any]]:
        if not self._configure() or not self.settings.simbad_enabled:
            return None
        if not self._allow_query():
            log.info("SIMBAD per-request budget exhausted; skipping %s", identifier)
            return None

        last_exc: Optional[Exception] = None
        for attempt in range(self.settings.simbad_max_retries):
            try:
                table = self._simbad.query_object(identifier)
                if table is None or len(table) == 0:
                    return None  # definitive "no such object"
                row = table[0]
                return {col: row[col] for col in table.colnames}
            except Exception as exc:
                last_exc = exc
                name = type(exc).__name__
                # A genuine "not found" should not be retried.
                if "not found" in str(exc).lower() or name in ("EmptyReplyError",):
                    return None
                backoff = self.settings.simbad_retry_backoff * (2 ** attempt)
                log.warning(
                    "SIMBAD query %r failed (%s: %s); retrying in %.1fs",
                    identifier, name, str(exc)[:120], backoff,
                )
                time.sleep(backoff)
        log.error("SIMBAD query %r gave up: %s", identifier, last_exc)
        return None

    def _query_vizier_gcvs(self, identifier: str) -> Optional[Dict[str, Any]]:
        """Fallback: look the star up in the GCVS catalogue (II/250) on VizieR.

        One request, and it also yields the canonical ``VarName`` (e.g.
        ``bet   Per``) which is useful for display.
        """
        if not self.settings.simbad_enabled or not self._allow_query():
            return None
        try:
            from astroquery.vizier import Vizier

            viz = Vizier(columns=["**"], row_limit=1)
            viz.ROW_LIMIT = 1
            tables = viz.query_object(identifier, catalog="B/gcvs")
            if not tables or len(tables[0]) == 0:
                return None
            row = tables[0][0]
            return {col: row[col] for col in tables[0].colnames}
        except Exception as exc:
            log.debug("VizieR GCVS fallback failed for %r: %s", identifier, exc)
            return None

    # ------------------------------------------------------------------ #
    # Normalisation
    # ------------------------------------------------------------------ #
    @staticmethod
    def _to_float(value: Any) -> Optional[float]:
        if value is None:
            return None
        try:
            text = str(value).strip()
            if not text or text in {"--", "---", "nan", "None", "masked"}:
                return None
            out = float(text)
        except (TypeError, ValueError):
            return None
        if out != out:  # NaN
            return None
        return out

    def _normalise(self, identifier: str, row: Dict[str, Any], source: str) -> SimbadResult:
        ra = self._to_float(row.get("ra", row.get("RAJ2000")))
        dec = self._to_float(row.get("dec", row.get("DEJ2000")))
        if ra is None or dec is None:
            return SimbadResult(
                identifier=identifier, found=False, negative=False,
                source=source, tried=[identifier],
                reason="response had no usable coordinates",
            )
        # VizieR GCVS returns RA in hours ("03 08 10.13") -> convert.
        if source == "vizier" and "RAJ2000" in row:
            ra = _sexagesimal_hours_to_deg(str(row["RAJ2000"]))
            dec = _sexagesimal_deg_to_deg(str(row["DEJ2000"]))
            if ra is None or dec is None:
                return SimbadResult(
                    identifier=identifier, found=False, source=source,
                    tried=[identifier], reason="could not parse GCVS coordinates",
                )

        v_mag = self._to_float(row.get("V", row.get("magMax")))
        main_id = row.get("main_id") or row.get("VarName") or row.get("GCVS")
        return SimbadResult(
            identifier=identifier,
            found=True,
            ra_deg=round(float(ra), 7),
            dec_deg=round(float(dec), 7),
            v_mag=v_mag,
            main_id=str(main_id).strip() if main_id else None,
            object_type=str(row.get("otype", "")).strip() or None,
            spectral_type=str(row.get("sp_type", row.get("SpType", ""))).strip() or None,
            source=source,
            tried=[identifier],
        )

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def resolve(
        self,
        candidates: Sequence[str],
        catalog_ra_deg: Optional[float] = None,
        catalog_dec_deg: Optional[float] = None,
        force_refresh: bool = False,
    ) -> SimbadResult:
        """Resolve one star. ``candidates`` is an ordered list of identifiers.

        ``catalog_ra_deg``/``catalog_dec_deg`` are used both as the final
        fallback and as a cross-match sanity check on whatever SIMBAD returns.
        """
        if not candidates:
            return SimbadResult(identifier="", found=False, negative=True, reason="no candidates")

        primary = candidates[0]
        started = time.monotonic()

        if not force_refresh:
            for candidate in candidates:
                cached = self.cache_get(candidate)
                if cached is not None:
                    cached.identifier = primary
                    cached.elapsed_ms = int((time.monotonic() - started) * 1000)
                    return cached

        tried: List[str] = []
        budget_exhausted = False
        # ---- 1. SIMBAD by name ------------------------------------- #
        for candidate in candidates:
            tried.append(candidate)
            if not self._budget_available():
                budget_exhausted = True
                break
            row = self._query_simbad(candidate)
            if row is None:
                continue
            result = self._normalise(candidate, row, "simbad")
            if not result.found:
                continue
            sep = _crossmatch_arcsec(
                result.ra_deg, result.dec_deg, catalog_ra_deg, catalog_dec_deg
            )
            result.crossmatch_arcsec = sep
            if sep is not None and sep > self.settings.simbad_max_crossmatch_arcsec:
                log.warning(
                    "SIMBAD match for %r is %.0f\" from catalogue position -- rejecting",
                    candidate, sep,
                )
                continue
            result.identifier = primary
            result.tried = tried
            result.elapsed_ms = int((time.monotonic() - started) * 1000)
            self.cache_put(result)
            return result

        # ---- 2. VizieR / GCVS -------------------------------------- #
        for candidate in candidates[:2]:
            if not self._budget_available():
                budget_exhausted = True
                break
            row = self._query_vizier_gcvs(candidate)
            if row is None:
                continue
            result = self._normalise(candidate, row, "vizier")
            if not result.found:
                continue
            sep = _crossmatch_arcsec(
                result.ra_deg, result.dec_deg, catalog_ra_deg, catalog_dec_deg
            )
            result.crossmatch_arcsec = sep
            if sep is not None and sep > self.settings.simbad_max_crossmatch_arcsec:
                continue
            result.identifier = primary
            result.tried = tried
            result.elapsed_ms = int((time.monotonic() - started) * 1000)
            self.cache_put(result)
            return result

        # ---- 3. Catalogue coordinates ------------------------------ #
        if catalog_ra_deg is not None and catalog_dec_deg is not None:
            result = SimbadResult(
                identifier=primary,
                found=True,
                ra_deg=catalog_ra_deg,
                dec_deg=catalog_dec_deg,
                source="catalog",
                tried=tried,
                reason="SIMBAD unresolved; using allstars-cat.txt position",
                elapsed_ms=int((time.monotonic() - started) * 1000),
            )
            self.cache_put(result)
            return result

        # ---- 4. Negative cache ------------------------------------- #
        # Only cache a *definitive* miss. A budget-limited give-up is not a
        # fact about the sky and must be retried on a later request.
        result = SimbadResult(
            identifier=primary,
            found=False,
            negative=not budget_exhausted,
            budget_exhausted=budget_exhausted,
            source="none",
            tried=tried,
            reason=(
                "per-request SIMBAD budget exhausted"
                if budget_exhausted
                else "no SIMBAD/GCVS match and no catalogue position"
            ),
            elapsed_ms=int((time.monotonic() - started) * 1000),
        )
        if result.negative:
            self.cache_put(result)
        return result

    def resolve_many(
        self,
        jobs: Iterable[Dict[str, Any]],
        max_workers: Optional[int] = None,
    ) -> Dict[str, SimbadResult]:
        """Resolve many stars concurrently.

        ``jobs`` items::

            {"star_id": str, "candidates": [str, ...],
             "ra_deg": float|None, "dec_deg": float|None}

        Returns a dict keyed by ``star_id``. Cache hits are separated out so we
        only spend thread-pool slots (and CDS requests) on genuine misses.
        """
        jobs = list(jobs)
        results: Dict[str, SimbadResult] = {}
        pending: List[Dict[str, Any]] = []

        for job in jobs:
            star_id = job["star_id"]
            cached = None
            for candidate in job.get("candidates", []):
                cached = self.cache_get(candidate)
                if cached is not None:
                    break
            if cached is not None:
                cached.identifier = job.get("candidates", [star_id])[0] if job.get("candidates") else star_id
                results[star_id] = cached
            else:
                pending.append(job)

        if not pending:
            return results

        workers = max_workers or self.settings.simbad_max_workers
        workers = max(1, min(workers, len(pending)))
        log.info("Resolving %d star(s) via SIMBAD with %d worker(s)", len(pending), workers)

        # Submit in the caller's priority order and collect in the same order.
        # Using as_completed() here would let the *last* jobs grab the budget,
        # which is exactly backwards: the caller puts the stars it intends to
        # display first.
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="simbad") as pool:
            futures = [
                (
                    job["star_id"],
                    pool.submit(
                        self.resolve,
                        job["candidates"],
                        job.get("ra_deg"),
                        job.get("dec_deg"),
                        job.get("force_refresh", False),
                    ),
                )
                for job in pending
            ]
            for star_id, future in futures:
                try:
                    results[star_id] = future.result()
                except Exception as exc:  # pragma: no cover
                    log.error("SIMBAD worker failed for %s: %s", star_id, exc)
                    results[star_id] = SimbadResult(
                        identifier=star_id, found=False, negative=True,
                        reason=f"worker error: {exc}",
                    )
        return results

    def warm(self, identifiers: Sequence[str]) -> Dict[str, Any]:
        """Pre-populate the cache (used by ``scripts/warm_cache.py``)."""
        jobs = [{"star_id": ident, "candidates": [ident]} for ident in identifiers]
        out = self.resolve_many(jobs)
        return {
            "requested": len(identifiers),
            "resolved": sum(1 for r in out.values() if r.found),
            "unresolved": sum(1 for r in out.values() if not r.found),
            "live_queries": self.queries_issued,
        }


# ---------------------------------------------------------------------- #
# Helpers
# ---------------------------------------------------------------------- #
def _crossmatch_arcsec(
    ra1: Optional[float], dec1: Optional[float],
    ra2: Optional[float], dec2: Optional[float],
) -> Optional[float]:
    if None in (ra1, dec1, ra2, dec2):
        return None
    import math

    phi1, phi2 = math.radians(dec1), math.radians(dec2)
    dphi = phi2 - phi1
    dlam = math.radians(ra2 - ra1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return math.degrees(2 * math.asin(min(1.0, math.sqrt(a)))) * 3600.0


def _sexagesimal_hours_to_deg(text: str) -> Optional[float]:
    parts = text.replace("h", " ").replace("m", " ").replace("s", " ").split()
    try:
        h, m, s = (float(parts[0]), float(parts[1]), float(parts[2]))
    except (IndexError, ValueError):
        try:
            return float(text) * 15.0
        except ValueError:
            return None
    sign = -1.0 if text.strip().startswith("-") else 1.0
    return sign * (abs(h) + m / 60.0 + s / 3600.0) * 15.0


def _sexagesimal_deg_to_deg(text: str) -> Optional[float]:
    parts = text.replace("d", " ").replace("'", " ").replace('"', " ").split()
    try:
        d, m, s = (float(parts[0]), float(parts[1]), float(parts[2]))
    except (IndexError, ValueError):
        try:
            return float(text)
        except ValueError:
            return None
    sign = -1.0 if text.strip().startswith("-") else 1.0
    return sign * (abs(d) + m / 60.0 + s / 3600.0)


# ---------------------------------------------------------------------- #
# Process-wide singleton (FastAPI dependency)
# ---------------------------------------------------------------------- #
_CLIENT: Optional[SimbadClient] = None
_CLIENT_LOCK = threading.Lock()


def get_simbad_client() -> SimbadClient:
    global _CLIENT
    if _CLIENT is None:
        with _CLIENT_LOCK:
            if _CLIENT is None:
                _CLIENT = SimbadClient()
    return _CLIENT
