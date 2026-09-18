@echo off
REM Eclipse Hunter - Windows APK Build Automation
REM Usage:
REM   build_apk.bat
REM   build_apk.bat https://your-api.fly.dev
REM   set API_BASE_URL=https://your-api.fly.dev && build_apk.bat

setlocal enabledelayedexpansion

REM --- Config ---
set "PROJECT_DIR=%~dp0.."
set "FRONTEND_DIR=%PROJECT_DIR%\frontend"

REM Backend URL priority: arg1 > env API_BASE_URL > default emulator
if not "%~1"=="" (
    set "API_BASE_URL=%~1"
)
if "%API_BASE_URL%"=="" (
    set "API_BASE_URL=http://10.0.2.2:8000"
)

echo ==============================================
echo Eclipse Hunter - Release APK Builder
echo ==============================================
echo Frontend dir : %FRONTEND_DIR%
echo Backend URL  : %API_BASE_URL%
echo Time         : %date% %time%
echo.

REM --- Checks ---
where flutter >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] flutter not found on PATH. Install Flutter SDK and add to PATH.
    exit /b 1
)

if not exist "%FRONTEND_DIR%\pubspec.yaml" (
    echo [ERROR] pubspec.yaml not found at %FRONTEND_DIR%
    exit /b 1
)

cd /d "%FRONTEND_DIR%"

echo [1/4] Cleaning...
call flutter clean
if %errorlevel% neq 0 (
    echo [ERROR] flutter clean failed
    exit /b 1
)

echo [2/4] Getting dependencies...
call flutter pub get
if %errorlevel% neq 0 (
    echo [ERROR] flutter pub get failed
    exit /b 1
)

echo [3/4] Analyzing (warnings only)...
call flutter analyze
REM Don't fail on analyze warnings, just show them

echo [4/4] Building release APK with API_BASE_URL=%API_BASE_URL%
call flutter build apk --release --dart-define=API_BASE_URL=%API_BASE_URL%
if %errorlevel% neq 0 (
    echo [ERROR] flutter build apk failed
    exit /b 1
)

echo.
echo ==============================================
echo Build succeeded!
echo ==============================================
dir /b build\app\outputs\flutter-apk\app-*.apk
echo.
echo Universal APK: %FRONTEND_DIR%\build\app\outputs\flutter-apk\app-release.apk

REM Copy with timestamp to project root
for /f "tokens=2 delims==" %%I in ('wmic os get localdatetime /value') do set datetime=%%I
set "TIMESTAMP=%datetime:~0,8%-%datetime:~8,6%"
set "DEST=%PROJECT_DIR%\eclipse-hunter-%TIMESTAMP%.apk"
copy /y "build\app\outputs\flutter-apk\app-release.apk" "%DEST%"
echo Copied to: %DEST%

echo.
echo To install on connected device:
echo   adb install "%DEST%"
echo.
echo To build for production with your own backend:
echo   build_apk.bat https://your-api.fly.dev
echo.

endlocal
