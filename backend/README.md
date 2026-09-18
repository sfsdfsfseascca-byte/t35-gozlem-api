# Eclipse Hunter - Backend

Predicts highly observable eclipsing binary minima for a given night and location.

**Ephemeris source:** Kreiner (2004) TIDAK database, `https://www.as.up.krakow.pl/ephem/` - EPHEM.TXT (linear elements M0 + N*Period) + allstars-cat.txt (J2000 coordinates, V magnitudes, variability type).

## Architecture

```
EPHEM.TXT (M0/Period) ─┐
                        ├─► build_stars() join on (NAME, CONST) ─► 3828 unique stars
allstars-cat.txt (RA/Dec/V) ─┘

Night Query (date + lat/lon/tz)
  1. minima_in_window() pure arithmetic T = M0 + N*P (free)
  2. declination plausibility + eclipsing-type filter (free)
  3. batched astropy AltAz geometry for catalog coords (vectorized, ~2s for 5000 candidates)
  4. SIMBAD verification (cached, budgeted, negative-cached)
  5. score + sort
```

### Key Correctness Fixes

- **HJD -> geocentric correction**: `JD_geo = HJD - (r_sun->earth . r_hat_star)/c`, iterated, matches `astropy.time.Time.light_travel_time(kind='heliocentric')` to <0.1s. Skipping it introduces up to ±8.3 min error.
- **RA units**: allstars-cat.txt stores RA in *hours*, not degrees. RZ Cas 2h48m55.5s = 42.23125° matches SIMBAD to 0.14".
- **Name normalisation**: EPHEM.TXT `SCO MU1` vs catalog `mu. 1 Sco` - fuzzy join via `normalize_star_name()`.
- **Variability filter**: rejects novae (`NB+EA` DQ Her, `NA` V1500 Cyg) whose magnitude range is outburst amplitude, not eclipse depth.
- **Etc/GMT sign**: POSIX zones invert sign - `Etc/GMT-2` is UTC+2. Fixed.
- **Southern hemisphere declination limit**: returns (dec_min, dec_max) band, not just lower bound.
- **SIMBAD anti-ban discipline**: 45-day positive cache, 12h negative cache, per-request budget (250), concurrency cap (12), cross-match guard (300").
- **Logging race**: `logging.getLogger("astroquery")` before import creates plain Logger, breaking astroquery's `_init_log()`.

### Validated Against

- **Sky & Telescope** Algol minima Jan 9 2026 21:02 EST and Feb 1 2026 19:36 EST: predicted within 20 min (real ephemeris drift over 3000 cycles).
- Catalog positions vs SIMBAD: RZ Cas 0.14", R CMa 24" (proper motion).

## Run Locally

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
# docs at http://localhost:8000/docs
```

### With Redis

```bash
docker run -d -p 6379:6379 redis:7-alpine
EH_REDIS_URL=redis://localhost:6379/0 uvicorn app.main:app --reload
```

### Docker

```bash
docker build -t eclipse-hunter-api .
docker run -p 8000:8000 -e EH_REDIS_URL=redis://host.docker.internal:6379/0 eclipse-hunter-api
```

### Tests

```bash
pytest -q
# 124 tests, offline by default
```

## API

- `POST /api/v1/night` - main prediction
- `GET /api/v1/night?date=2026-09-20&lat=38.72&lon=35.48&timezone=Europe/Istanbul`
- `GET /api/v1/tonight?lat=38.72&lon=35.48`
- `GET /health`
- `GET /api/v1/catalog/stats`
- `GET /api/v1/simbad/{identifier}`

Example:

```bash
curl -X POST http://localhost:8000/api/v1/night \
  -H "Content-Type: application/json" \
  -d '{
    "date": "2026-09-20",
    "location": {"latitude": 38.7208, "longitude": 35.4875, "timezone": "Europe/Istanbul"},
    "max_vmag": 9.5,
    "min_altitude_deg": 30,
    "min_moon_separation_deg": 30
  }' | jq '.events[0]'
```

## Deployment (Fly.io / Render / Cloud Run)

Set env vars:
- `EH_REDIS_URL` - Upstash Redis or similar
- `EH_API_KEY` - optional bearer for protection
- `EH_USER_AGENT` - with contact email (polite to Krakow server)
- `EH_CORS_ORIGINS` - your frontend domain

The app bundles `data/EPHEM.TXT` and `data/allstars-cat.txt` so cold start works with zero network. It refreshes from Krakow every 24h with conditional HTTP (304).

## Citation

```
Kreiner, J.M. 2004, Acta Astronomica 54, 207
Database: TIDAK, Astronomy Dept, University of National Education Commission, Krakow
https://www.as.up.krakow.pl/ephem/
```
