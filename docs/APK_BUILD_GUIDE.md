# APK Generation Guide - Eclipse Hunter

## Prerequisites

1. Flutter SDK >=3.16.0 installed and on PATH
   - https://docs.flutter.dev/get-started/install
   - Verify: `flutter doctor`

2. Android SDK + Build Tools
   - Android Studio or command-line tools
   - `flutter doctor --android-licenses` accepted

3. Java 17 (required for AGP 8.1.0)

## Android-Specific Configurations

### 1. Permissions (`AndroidManifest.xml`)

File: `frontend/android/app/src/main/AndroidManifest.xml`

```xml
<uses-permission android:name="android.permission.INTERNET" />
<uses-permission android:name="android.permission.ACCESS_FINE_LOCATION" />
<uses-permission android:name="android.permission.ACCESS_COARSE_LOCATION" />
```

- `INTERNET`: required for `https://www.as.up.krakow.pl/ephem/` via backend, and for backend API itself.
- `ACCESS_FINE_LOCATION` + `ACCESS_COARSE_LOCATION`: GPS via `geolocator` plugin.

Debug manifest (`android/app/src/debug/AndroidManifest.xml`) adds:

```xml
<application android:usesCleartextTraffic="true" />
```

This allows `http://10.0.2.2:8000` (emulator host loopback) during development. Release builds use HTTPS only.

### 2. SDK Versions (`build.gradle`)

File: `frontend/android/app/build.gradle`

```gradle
android {
    namespace "com.eclipsehunter.app"
    compileSdk 34

    defaultConfig {
        applicationId "com.eclipsehunter.app"
        minSdk 23        // geolocator requires >=21, we use 23 for permission model
        targetSdk 34
        versionCode flutterVersionCode.toInteger()
        versionName flutterVersionName
    }
    buildTypes {
        release {
            signingConfig signingConfigs.debug // replace with real keystore for Play Store
            minifyEnabled true
            shrinkResources true
        }
    }
}
dependencies {
    implementation 'com.google.android.gms:play-services-location:21.0.1'
}
```

Root `android/build.gradle` sets `buildDir = "../build"` to keep Flutter's build system happy.

`android/settings.gradle` loads Flutter SDK from `local.properties`:

```
sdk.dir=/path/to/Android/sdk
flutter.sdk=/path/to/flutter
```

### 3. ProGuard (`proguard-rules.pro`)

Keeps Flutter and geolocator classes when R8 minification is on.

## Step-by-Step Terminal Commands

### Linux / macOS

```bash
cd frontend

# 0. Ensure backend URL is set (emulator vs physical device vs production)
# Emulator:
export API_BASE_URL=http://10.0.2.2:8000
# Physical device on same WiFi (replace with your machine IP):
export API_BASE_URL=http://192.168.1.42:8000
# Production:
export API_BASE_URL=https://your-api.fly.dev

# 1. Clean previous builds
flutter clean

# 2. Get dependencies
flutter pub get

# 3. (Optional) Run analyzer
flutter analyze

# 4. Build release APK with backend URL injected
flutter build apk --release \
  --dart-define=API_BASE_URL=$API_BASE_URL \
  --split-per-abi

# Or single universal APK:
flutter build apk --release \
  --dart-define=API_BASE_URL=$API_BASE_URL

# 5. Output location
ls -lh build/app/outputs/flutter-apk/
# app-release.apk (universal) or app-arm64-v8a-release.apk etc.

# 6. Install to connected device
adb install build/app/outputs/flutter-apk/app-release.apk
```

### Windows (PowerShell)

```powershell
cd frontend
$env:API_BASE_URL="https://your-api.fly.dev"
flutter clean
flutter pub get
flutter build apk --release --dart-define=API_BASE_URL=$env:API_BASE_URL
```

## Windows Batch Automation Script

File: `scripts/build_apk.bat`

- Cleans, gets deps, builds release APK with backend URL defined
- Handles missing env var with default
- Copies APK to project root with timestamp

Usage:

```bat
REM Default (emulator):
scripts\build_apk.bat

REM Production:
set API_BASE_URL=https://eclipse-hunter.fly.dev
scripts\build_apk.bat

REM Or pass as arg:
scripts\build_apk.bat https://eclipse-hunter.fly.dev
```

See the script itself for full implementation.

## Troubleshooting

- `SDK location not found`: create `android/local.properties` with `sdk.dir=...` and `flutter.sdk=...`
- `usesCleartextTraffic`: release APK will block HTTP by default; use HTTPS for production backend
- `geolocator` permission denied: ensure `ACCESS_FINE_LOCATION` in manifest and handle runtime permission (app does)
- `minSdk 23` error: update `android/app/build.gradle` minSdk
- APK size: `--split-per-abi` produces 3 smaller APKs; Play Store prefers App Bundle (`flutter build appbundle`)

## Backend URL Strategy

- Development (emulator): `http://10.0.2.2:8000` -> host's localhost:8000
- Development (physical): `http://<your-lan-ip>:8000`
- Production: `https://<your-fly-app>.fly.dev` or similar

The URL is baked at compile time via `--dart-define`. Changing backend does NOT require code change, only rebuild.
