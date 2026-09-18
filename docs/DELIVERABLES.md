# Deliverables - Eclipse Hunter

## Backend (Python FastAPI)

| File | Purpose |
|------|---------|
| `backend/app/config.py` | Central settings, env-driven, jittered TTLs |
| `backend/app/cache.py` | `InMemoryCache` LRU+TTL + `RedisCache` + key builders + negative caching |
| `backend/app/ephemeris.py` | Parsers for `EPHEM.TXT` (M0/Period) and `allstars-cat.txt` (RA in hours, Vmax/Vmin, type). Handles: header rows, `(*)`, lowercase `pri`, missing spectral type desync, `CMa/CMi` IAU codes, punctuation mismatch `SCO MU1` vs `mu. 1 Sco`, nova filter |
| `backend/app/astro_engine.py` | `minima_in_window()` solves `T=M0+N*P`, `heliocentric_to_geocentric()` = `JD_geo = HJD - (r_sun->earth·r_hat)/c` iterated, matches astropy builtin to 0.1s, `observe_events_batch()` vectorized AltAz for 4000 stars, `declination_limit()` both hemispheres, scoring |
| `backend/app/simbad.py` | Cached SIMBAD ladder: cache -> SIMBAD name variants (6) -> VizieR GCVS -> catalog fallback -> negative cache. Budget 250, workers 12, cross-match guard 300", preconfigure() on main thread to avoid import race |
| `backend/app/datasource.py` | 4-layer cache: memory singleton, shared cache (Redis), disk `data/`, conditional HTTP ETag/If-Modified-Since |
| `backend/app/predictor.py` | Pipeline: arithmetic -> dec plausibility + eclipsing-type filter -> batched geometry (95% removed before SIMBAD) -> SIMBAD only for survivors -> LTT correction for catalog-less stars -> score/sort |
| `backend/app/models.py` | Pydantic schemas |
| `backend/app/deps.py` | API key + sliding-window rate limiter |
| `backend/app/main.py` | FastAPI app, lifespan warms ephemeris + astroquery, fixes logging race (`getLogger("astroquery")` before import breaks `_set_defaults`) |
| `backend/app/routers/night.py` | `POST /api/v1/night`, `GET /api/v1/night`, `/tonight`, `/defaults` |
| `backend/app/routers/system.py` | `/health`, `/catalog/stats`, `/simbad/{id}`, `/admin/refresh-ephemeris` |
| `backend/data/EPHEM.TXT` | Bundled snapshot |
| `backend/data/allstars-cat.txt` | Bundled snapshot |
| `backend/requirements.txt` | Pinned deps |
| `backend/Dockerfile` | Python 3.13 slim, healthcheck |
| `backend/tests/` | 142 tests, offline |

### Backend Logic Highlights

**Scraper per requirements:**
> When a date is requested, retrieve ephemeris from `https://www.as.up.krakow.pl/ephem/`. Instead of scraping HTML, download `EPHEM.TXT` and calculate next minima server-side using `T=M0+N*Period`.

Implemented in `datasource.py` + `ephemeris.py` + `astro_engine.py:minima_in_window()`.

**SIMBAD Verification:**
> Query `astroquery.simbad` for J2000 + V mag, with caching including negative caching to prevent IP ban.

Implemented in `simbad.py`: 45-day positive TTL, 12h negative TTL, jitter, budget, cross-match guard.

**Astropy Engine:**
> Using user location + date, calculate target Altitude and Moon separation at minimum, filter altitude<30 or Moon sep<30.

Implemented in `astro_engine.py:observe_events_batch()` vectorized.

## Frontend (Flutter)

| File | Purpose |
|------|---------|
| `frontend/pubspec.yaml` | Dependencies: http, geolocator, shared_preferences, intl, google_fonts |
| `frontend/lib/main.dart` | Entry, dark theme |
| `frontend/lib/theme/app_theme.dart` | Dark theme, gradients |
| `frontend/lib/models/eclipse_event.dart` | Mirrors backend Pydantic, fromJson |
| `frontend/lib/services/api_service.dart` | `API_BASE_URL` via `--dart-define`, POST /night |
| `frontend/lib/services/location_service.dart` | GPS + saved location, Kayseri default 38.7208,35.4875 |
| `frontend/lib/screens/home_screen.dart` | Date picker + location input + GPS + filter chip summary + Find button |
| `frontend/lib/screens/results_screen.dart` | FutureBuilder + ListView + pull-to-refresh |
| `frontend/lib/widgets/event_card.dart` | Displays: Star Name, Local Time of Minimum, V Magnitude, Peak Altitude, Angular Distance to Moon (per requirements) |
| `frontend/lib/widgets/filter_sheet.dart` | Sliders for filters |
| `frontend/android/app/src/main/AndroidManifest.xml` | Permissions: INTERNET, ACCESS_FINE_LOCATION, ACCESS_COARSE_LOCATION |
| `frontend/android/app/build.gradle` | compileSdk 34, minSdk 23, targetSdk 34, play-services-location |
| `frontend/android/build.gradle` | root build |
| `frontend/android/settings.gradle` | Flutter plugin loader |

## APK Generation

- **Manifest:** `frontend/android/app/src/main/AndroidManifest.xml`
- **Gradle SDK:** `frontend/android/app/build.gradle` - see file
- **Commands:** `docs/APK_BUILD_GUIDE.md`
- **Batch script:** `scripts/build_apk.bat` (Windows) + `scripts/build_apk.sh` (Linux/macOS)

Snippet from `.bat`:

```bat
flutter clean
flutter pub get
flutter build apk --release --dart-define=API_BASE_URL=%API_BASE_URL%
copy /y "build\app\outputs\flutter-apk\app-release.apk" "eclipse-hunter-%TIMESTAMP%.apk"
```

## Testing

```bash
cd backend
pytest -q
# 142 passed
```

## Deployment

- `deploy/fly.toml`, `deploy/render.yaml`, `deploy/docker-compose.yml`
- Env: `EH_REDIS_URL`, `EH_API_KEY`, `EH_USER_AGENT`
- Health: `/health`
