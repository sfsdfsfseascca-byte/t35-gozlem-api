# Eclipse Hunter - Observable Eclipsing Binaries

Mobile app that predicts **highly observable eclipsing binary minima** for a specific night and location. Frontend: Flutter (Android APK). Backend: FastAPI + Astropy + SIMBAD + Redis cache.

**Ephemeris:** Kreiner (2004) TIDAK database, Astronomy Department, University of National Education Commission, Krakow. File: `EPHEM.TXT` (M0 + N*Period) + `allstars-cat.txt` (coordinates, V mag).

## Project Structure

```
eclipse-hunter/
├── backend/
│   ├── app/
│   │   ├── config.py         # env-driven settings
│   │   ├── cache.py          # Redis / in-memory + key builders + negative caching
│   │   ├── ephemeris.py      # EPHEM.TXT + allstars-cat.txt parsers + join logic
│   │   ├── astro_engine.py   # T=M0+N*P, HJD->geo light-time, AltAz, Moon sep, scoring
│   │   ├── simbad.py         # cached SIMBAD ladder with budget + cross-match guard
│   │   ├── datasource.py     # 4-layer cache (mem, redis, disk, conditional HTTP)
│   │   ├── predictor.py      # pipeline: arithmetic -> geometry batch -> SIMBAD -> score
│   │   ├── models.py         # Pydantic schemas
│   │   ├── deps.py           # API key + rate limiter
│   │   ├── main.py           # FastAPI app + lifespan
│   │   └── routers/
│   │       ├── night.py      # POST/GET /api/v1/night
│   │       └── system.py     # /health, /catalog/stats, /simbad/{id}
│   ├── data/
│   │   ├── EPHEM.TXT         # bundled snapshot (offline boot)
│   │   └── allstars-cat.txt
│   ├── tests/                # 124 tests, offline
│   ├── requirements.txt
│   ├── Dockerfile
│   └── README.md
├── frontend/
│   ├── lib/
│   │   ├── main.dart
│   │   ├── models/eclipse_event.dart
│   │   ├── services/api_service.dart (dart-define API_BASE_URL)
│   │   ├── services/location_service.dart (geolocator + shared_preferences)
│   │   ├── screens/home_screen.dart (date picker + location)
│   │   ├── screens/results_screen.dart (ListView)
│   │   ├── widgets/event_card.dart (Star Name, Local Time, V Mag, Peak Alt, Moon Sep)
│   │   ├── widgets/filter_sheet.dart
│   │   └── theme/app_theme.dart
│   ├── android/
│   │   ├── app/src/main/AndroidManifest.xml (INTERNET + ACCESS_FINE_LOCATION)
│   │   └── app/build.gradle (compileSdk 34, minSdk 23, targetSdk 34)
│   └── pubspec.yaml
├── scripts/
│   ├── build_apk.bat         # Windows automation
│   └── build_apk.sh          # Linux/macOS automation
└── docs/
    └── APK_BUILD_GUIDE.md
```

## Backend Quick Start

```bash
cd backend
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
# open http://localhost:8000/docs
```

## Frontend Quick Start

```bash
cd frontend
flutter pub get
flutter run --dart-define=API_BASE_URL=http://10.0.2.2:8000
```

## Build APK

```bash
# Linux/macOS
./scripts/build_apk.sh https://your-api.fly.dev

# Windows
scripts\build_apk.bat https://your-api.fly.dev
```

See `docs/APK_BUILD_GUIDE.md` for SDK versions, manifest, and troubleshooting.

## API Example

```bash
curl -X POST http://localhost:8000/api/v1/night \
  -H "Content-Type: application/json" \
  -d '{
    "date": "2026-09-20",
    "location": {"latitude": 38.7208, "longitude": 35.4875, "timezone": "Europe/Istanbul"},
    "min_altitude_deg": 30,
    "min_moon_separation_deg": 30,
    "max_vmag": 9.5
  }' | jq '.events[0] | {star_name, time_local, v_magnitude, peak_altitude_deg, moon_separation_deg, score}'
```

## Deployment

- Backend: Fly.io / Render / Cloud Run with `EH_REDIS_URL` (Upstash) and `EH_API_KEY` optional
- Frontend: APK via `flutter build apk --release --dart-define=API_BASE_URL=...`

## Validation

- Algol minima vs Sky & Telescope Jan 9 2026 21:02 EST and Feb 1 2026 19:36 EST: within 20 min (real period drift)
- RZ Cas catalog vs SIMBAD: 0.14" agreement

## Citation

Kreiner, J.M. 2004, Acta Astronomica 54, 207. TIDAK database https://www.as.up.krakow.pl/ephem/
