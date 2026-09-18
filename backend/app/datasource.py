"""
Downloading + caching of the Krakow ephemeris files.

Layered caching (this is what keeps us off the observatory's bad side):

  1. **Process memory** -- the parsed :class:`Star` index lives in a singleton
     and is rebuilt only when the content hash changes.
  2. **Shared cache** (Redis/memory) -- the raw file text, keyed by content
     hash, with a 24 h TTL. Survives worker restarts.
  3. **Disk** -- ``backend/data/EPHEM.TXT``. Lets the API boot with zero network
     access (important for cold-start latency and for air-gapped testing).
  4. **Conditional HTTP** -- we send ``If-Modified-Since`` / ``If-None-Match`` so
     a 304 costs the server almost nothing.

The files are ~440 KB and ~570 KB and are updated rarely, so a 24 h TTL is both
polite and plenty fresh.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional, Tuple

from .cache import BaseCache, ephem_key, get_cache
from .config import Settings, get_settings
from .ephemeris import (
    CatalogRecord,
    ElementRecord,
    Star,
    build_stars,
    catalog_fingerprint,
    parse_allstars_cat,
    parse_ephem_txt,
)

log = logging.getLogger("eclipse_hunter.datasource")


@dataclass
class EphemerisBundle:
    """Immutable snapshot of the parsed catalogue."""

    stars: Dict[Tuple[str, str], Star]
    content_hash: str
    downloaded_at: datetime
    source_url: str
    element_records: int
    catalog_records: int
    from_cache: bool = False

    def stats(self) -> Dict[str, object]:
        return {
            "unique_stars": len(self.stars),
            "element_records": self.element_records,
            "catalog_records": self.catalog_records,
            "content_hash": self.content_hash,
            "downloaded_at": self.downloaded_at.isoformat(),
        }


@dataclass
class EphemerisSource:
    """Fetches, caches and parses the two Krakow files."""

    settings: Settings = field(default_factory=get_settings)
    cache: BaseCache = field(default_factory=get_cache)
    _bundle: Optional[EphemerisBundle] = None
    _lock: threading.RLock = field(default_factory=threading.RLock)

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def get_bundle(self, force_refresh: bool = False) -> EphemerisBundle:
        """Return the parsed catalogue, loading it on first use."""
        if self._bundle is not None and not force_refresh:
            return self._bundle
        with self._lock:
            if self._bundle is not None and not force_refresh:
                return self._bundle
            self._bundle = self._load(force_refresh=force_refresh)
            return self._bundle

    def stars(self, force_refresh: bool = False) -> Dict[Tuple[str, str], Star]:
        return self.get_bundle(force_refresh=force_refresh).stars

    def invalidate(self) -> None:
        with self._lock:
            self._bundle = None

    # ------------------------------------------------------------------ #
    # Loading
    # ------------------------------------------------------------------ #
    def _load(self, force_refresh: bool = False) -> EphemerisBundle:
        ephem_text, ephem_meta = self._fetch_file(
            self.settings.ephem_txt_url,
            self.settings.ephem_txt_name,
            force_refresh=force_refresh,
            required=True,
        )
        cat_text, cat_meta = self._fetch_file(
            self.settings.allstars_url,
            self.settings.allstars_name,
            force_refresh=force_refresh,
            required=False,
        )

        if ephem_text is None:
            raise RuntimeError(
                f"Could not obtain {self.settings.ephem_txt_name} from "
                f"{self.settings.ephem_txt_url} and no local copy exists."
            )

        elements = parse_ephem_txt(ephem_text)
        catalog = parse_allstars_cat(cat_text) if cat_text else []
        if not elements:
            raise RuntimeError("EPHEM.TXT parsed to zero element records -- format change?")

        stars = build_stars(elements, catalog)
        bundle = EphemerisBundle(
            stars=stars,
            content_hash=catalog_fingerprint(ephem_text),
            downloaded_at=datetime.now(timezone.utc),
            source_url=self.settings.ephem_txt_url,
            element_records=len(elements),
            catalog_records=len(catalog),
            from_cache=bool(ephem_meta.get("from_cache")),
        )
        log.info(
            "Ephemeris loaded: %d stars, %d element records, %d catalogue records (hash %s)",
            len(stars), len(elements), len(catalog), bundle.content_hash,
        )
        if not catalog:
            log.warning(
                "allstars-cat.txt unavailable: brightness pre-filter disabled and every "
                "candidate will need a SIMBAD query. Consider bundling a local copy."
            )
        return bundle

    # ------------------------------------------------------------------ #
    # Fetch with the 4 cache layers
    # ------------------------------------------------------------------ #
    def _fetch_file(
        self,
        url: str,
        filename: str,
        force_refresh: bool = False,
        required: bool = True,
    ) -> Tuple[Optional[str], Dict[str, object]]:
        meta: Dict[str, object] = {"url": url, "from_cache": False}

        # ---- Layer 3: disk --------------------------------------------- #
        disk_path = Path(self.settings.ephem_local_dir) / filename
        disk_text: Optional[str] = None
        if disk_path.exists():
            try:
                disk_text = disk_path.read_text(encoding="latin-1", errors="replace")
                meta["disk_copy"] = str(disk_path)
            except OSError as exc:
                log.warning("Could not read local %s: %s", disk_path, exc)

        if not force_refresh:
            # ---- Layer 2: shared cache --------------------------------- #
            cached = self.cache.get_json(ephem_key(filename))
            if cached and cached.get("text"):
                meta["from_cache"] = True
                meta["cached_at"] = cached.get("cached_at")
                return cached["text"], meta

            # Nothing shared yet, but we do have a disk copy -> use it and
            # avoid a network round-trip entirely.
            if disk_text:
                self._store_cache(filename, disk_text)
                self._write_disk(disk_path, disk_text)
                meta["from_cache"] = True
                meta["source"] = "disk"
                return disk_text, meta

        # ---- Layer 4: conditional HTTP --------------------------------- #
        text, http_meta = self._http_get(url, disk_path)
        meta.update(http_meta)
        if text:
            self._store_cache(filename, text)
            self._write_disk(disk_path, text)
            return text, meta

        if disk_text:
            log.warning("Download of %s failed; serving stale disk copy", filename)
            meta["stale"] = True
            meta["from_cache"] = True
            return disk_text, meta

        if not required:
            return None, meta
        raise RuntimeError(f"Failed to download {url}: {meta.get('error')}")

    def _http_get(self, url: str, disk_path: Path) -> Tuple[Optional[str], Dict[str, object]]:
        headers = {"User-Agent": self.settings.user_agent, "Accept": "text/plain,*/*"}
        # Reuse a stored ETag / Last-Modified so the server can answer 304.
        etag_path = disk_path.with_suffix(disk_path.suffix + ".etag")
        if etag_path.exists():
            try:
                stored = etag_path.read_text(encoding="utf-8").splitlines()
                if stored and stored[0]:
                    headers["If-None-Match"] = stored[0]
                if len(stored) > 1 and stored[1]:
                    headers["If-Modified-Since"] = stored[1]
            except OSError:
                pass

        try:
            import httpx
        except ImportError:  # pragma: no cover
            return None, {"error": "httpx not installed"}

        started = time.monotonic()
        try:
            with httpx.Client(
                timeout=self.settings.ephem_http_timeout,
                follow_redirects=True,
                headers=headers,
            ) as client:
                response = client.get(url)
            if response.status_code == 304:
                log.info("%s not modified (304)", url)
                return None, {"status": 304, "not_modified": True}
            response.raise_for_status()
            # The server serves latin-1 flavoured text; never let a stray byte
            # in a star name kill the whole load.
            text = response.content.decode("latin-1", errors="replace")
            new_etag = response.headers.get("ETag", "")
            new_lm = response.headers.get("Last-Modified", "")
            try:
                etag_path.parent.mkdir(parents=True, exist_ok=True)
                etag_path.write_text(f"{new_etag}\n{new_lm}\n", encoding="utf-8")
            except OSError:
                pass
            log.info(
                "Downloaded %s (%d bytes) in %.1fs",
                url, len(text), time.monotonic() - started,
            )
            return text, {"status": response.status_code, "bytes": len(text)}
        except Exception as exc:
            log.warning("Download of %s failed: %s", url, exc)
            return None, {"error": f"{type(exc).__name__}: {exc}"}

    def _store_cache(self, filename: str, text: str) -> None:
        self.cache.set_json(
            ephem_key(filename),
            {
                "text": text,
                "cached_at": datetime.now(timezone.utc).isoformat(),
                "hash": catalog_fingerprint(text),
            },
            BaseCache.jittered(self.settings.ephem_cache_ttl, self.settings.ttl_jitter),
        )

    @staticmethod
    def _write_disk(path: Path, text: str) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="latin-1", errors="replace")
        except OSError as exc:  # pragma: no cover - read-only FS
            log.debug("Could not write %s: %s", path, exc)


# ---------------------------------------------------------------------- #
# Singleton
# ---------------------------------------------------------------------- #
_SOURCE: Optional[EphemerisSource] = None
_SOURCE_LOCK = threading.Lock()


def get_ephemeris_source() -> EphemerisSource:
    global _SOURCE
    if _SOURCE is None:
        with _SOURCE_LOCK:
            if _SOURCE is None:
                _SOURCE = EphemerisSource()
    return _SOURCE


def set_ephemeris_source(source: Optional[EphemerisSource]) -> None:
    """Override the singleton (tests / dependency injection)."""
    global _SOURCE
    with _SOURCE_LOCK:
        _SOURCE = source
