"""Pre-warm SIMBAD cache for the brightest / most-observed eclipsing binaries.

Run once after deploy:

    python scripts/warm_cache.py --limit 200

This avoids a cold-start burst against CDS.
"""
import argparse
import sys
from pathlib import Path

# Allow running from backend/ root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.cache import get_cache
from app.datasource import get_ephemeris_source
from app.simbad import get_simbad_client


def main(limit: int = 200):
    source = get_ephemeris_source()
    bundle = source.get_bundle()
    # Brightest by Vmax
    candidates = [s for s in bundle.stars.values() if s.catalog and s.catalog.v_max]
    candidates.sort(key=lambda s: s.catalog.v_max)
    identifiers = [s.display_name for s in candidates[:limit]]

    print(f"Warming cache for {len(identifiers)} stars...")
    client = get_simbad_client()
    client.preconfigure()
    result = client.warm(identifiers)
    print(result)
    print("Cache stats:", get_cache().stats())


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=200)
    args = parser.parse_args()
    main(args.limit)
