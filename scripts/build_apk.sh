#!/usr/bin/env bash
# Eclipse Hunter - Linux/macOS APK Build Automation
# Usage:
#   ./scripts/build_apk.sh
#   API_BASE_URL=https://your-api.fly.dev ./scripts/build_apk.sh
#   ./scripts/build_apk.sh https://your-api.fly.dev

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FRONTEND_DIR="$PROJECT_DIR/frontend"

# Backend URL priority: $1 > $API_BASE_URL env > default emulator
API_URL="${1:-${API_BASE_URL:-http://10.0.2.2:8000}}"

echo "=============================================="
echo "Eclipse Hunter - Release APK Builder"
echo "=============================================="
echo "Frontend dir : $FRONTEND_DIR"
echo "Backend URL  : $API_URL"
echo "Time         : $(date)"
echo ""

if ! command -v flutter &>/dev/null; then
  echo "[ERROR] flutter not found on PATH"
  exit 1
fi

if [[ ! -f "$FRONTEND_DIR/pubspec.yaml" ]]; then
  echo "[ERROR] pubspec.yaml not found at $FRONTEND_DIR"
  exit 1
fi

cd "$FRONTEND_DIR"

echo "[1/4] Cleaning..."
flutter clean

echo "[2/4] Getting dependencies..."
flutter pub get

echo "[3/4] Analyzing..."
flutter analyze || true

echo "[4/4] Building release APK with API_BASE_URL=$API_URL"
flutter build apk --release --dart-define=API_BASE_URL="$API_URL"

echo ""
echo "=============================================="
echo "Build succeeded!"
echo "=============================================="
ls -lh build/app/outputs/flutter-apk/*.apk
echo ""
echo "Universal APK: $FRONTEND_DIR/build/app/outputs/flutter-apk/app-release.apk"

TIMESTAMP=$(date +%Y%m%d-%H%M%S)
DEST="$PROJECT_DIR/eclipse-hunter-$TIMESTAMP.apk"
cp build/app/outputs/flutter-apk/app-release.apk "$DEST"
echo "Copied to: $DEST"
echo ""
echo "Install: adb install $DEST"
