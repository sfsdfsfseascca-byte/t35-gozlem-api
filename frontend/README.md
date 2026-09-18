# Eclipse Hunter - Flutter Frontend

## Features

- Home Screen: Date Picker (evening of) + Location input with GPS button (geolocator) + saved location (shared_preferences)
- Results Screen: ListView of observable eclipsing binary minima
- Card fields: Star Name, Local Time of Minimum, V Magnitude, Peak Altitude, Angular Distance to Moon, plus score/quality, depth, uncertainty, type
- Filter sheet: min altitude (30°), min Moon separation (30°), max Vmag, min depth, include secondary, require dark sky, sort

Backend URL is injected at build time via `--dart-define=API_BASE_URL=...`. Default is `http://10.0.2.2:8000` for Android emulator.

## Run

```bash
flutter pub get
flutter run --dart-define=API_BASE_URL=http://10.0.2.2:8000
# physical device on same LAN:
flutter run --dart-define=API_BASE_URL=http://192.168.1.10:8000
```

## Build APK

See `docs/APK_BUILD_GUIDE.md` and `scripts/build_apk.bat` / `build_apk.sh`.

## Permissions

- `INTERNET` - backend API
- `ACCESS_FINE_LOCATION` + `ACCESS_COARSE_LOCATION` - GPS
