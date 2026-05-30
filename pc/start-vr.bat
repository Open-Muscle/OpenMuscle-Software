@echo off
REM ============================================================
REM   OpenMuscle VR launcher (Windows)
REM
REM   What it does, in order:
REM     1. Locate adb (PATH first, then Android SDK fallback)
REM     2. Verify a Quest is plugged in + ADB-authorized
REM     3. Start `openmuscle web` in its own console window
REM     4. Wait until the server responds on :8000
REM     5. Run `adb reverse tcp:8000 tcp:8000` so the headset
REM        can reach localhost
REM     6. Launch Quest Browser straight to /vr via ADB intent
REM
REM   Usage:   start-vr.bat                      (right arm, vr mode)
REM            start-vr.bat right                (same)
REM            start-vr.bat left                 (left arm, vr mode)
REM            start-vr.bat right ar             (right arm, AR passthrough)
REM            start-vr.bat left  ar             (left arm,  AR passthrough)
REM
REM   VR mode  = fully immersive black background (gesture training default)
REM   AR mode  = passthrough, you see your real workspace (field-capture)
REM
REM   To stop: close the "OpenMuscle VR Server" window.
REM ============================================================

setlocal enabledelayedexpansion

REM --- Configuration ---------------------------------------------------------
REM REPO is this script's own directory (i.e. pc/), so the launcher works
REM from any clone path without editing -- no hardcoded D: drive paths.
set "REPO=%~dp0"
if "%REPO:~-1%"=="\" set "REPO=%REPO:~0,-1%"
set "PORT=8000"
set "MAX_WAIT_S=20"

set "ARM=%~1"
if "%ARM%"=="" set "ARM=right"
if /I not "%ARM%"=="right" if /I not "%ARM%"=="left" (
    echo [ERROR] arm must be 'right' or 'left' (you passed: %ARM%^)
    pause
    exit /b 1
)
set "MODE=%~2"
if "%MODE%"=="" set "MODE=vr"
if /I not "%MODE%"=="vr" if /I not "%MODE%"=="ar" (
    echo [ERROR] mode must be 'vr' or 'ar' (you passed: %MODE%^)
    pause
    exit /b 1
)
set "URL=http://localhost:%PORT%/vr?arm=%ARM%&mode=%MODE%"

REM --- 1. Locate adb ---------------------------------------------------------
REM Prefer adb on PATH (the team's likely setup if they use MQDH or chocolatey),
REM fall back to the typical Android Studio install location.
set "ADB="
where adb >nul 2>nul
if not errorlevel 1 (
    set "ADB=adb"
) else if exist "%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe" (
    set "ADB=%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe"
)
if not defined ADB (
    echo [ERROR] adb not found on PATH or at:
    echo   %%LOCALAPPDATA%%\Android\Sdk\platform-tools\adb.exe
    echo Install Android Studio platform-tools or Meta Quest Developer Hub,
    echo or add adb to PATH.
    pause
    exit /b 1
)

REM --- 2. Verify Quest is authorized -----------------------------------------
REM Parse `adb devices` with for /f so we can tell "device" from "unauthorized"
REM or "offline" without depending on findstr's quirky tab/regex handling.
echo Checking Quest connection...
set "DEVICE_OK="
set "DEVICE_BAD="
for /f "skip=1 tokens=1,2" %%a in ('""%ADB%" devices" 2^>nul') do (
    if /I "%%b"=="device" set "DEVICE_OK=%%a"
    if /I "%%b"=="unauthorized" set "DEVICE_BAD=unauthorized: %%a"
    if /I "%%b"=="offline" set "DEVICE_BAD=offline: %%a"
)
if defined DEVICE_BAD (
    echo.
    echo [ERROR] Quest detected but %DEVICE_BAD%
    echo Put on the headset; the "Allow USB debugging?" dialog should appear.
    echo Tick "Always allow from this computer", tap Allow, re-run.
    pause
    exit /b 1
)
if not defined DEVICE_OK (
    echo.
    echo [ERROR] No Quest found over USB.
    echo   1. USB-connect the Quest
    echo   2. Put on the headset; accept the "Allow USB debugging?" dialog
    echo   3. Re-run this script
    echo.
    echo Current adb devices state:
    "%ADB%" devices
    pause
    exit /b 1
)
echo   Quest OK (%DEVICE_OK%^).

REM --- 3. Verify openmuscle is installed -------------------------------------
where openmuscle >nul 2>nul
if errorlevel 1 (
    echo [ERROR] openmuscle CLI not found on PATH.
    echo From the repo root: cd pc ^&^& pip install -e .
    pause
    exit /b 1
)

REM --- 4. Start the server in its own window ---------------------------------
REM cd into pc/ so openmuscle's default captures dir lands inside the repo.
echo Starting OpenMuscle web server on port %PORT%...
pushd "%REPO%"
start "OpenMuscle VR Server" cmd /k openmuscle web --port %PORT%
popd

REM --- 5. Wait for the server to respond -------------------------------------
echo Waiting up to %MAX_WAIT_S%s for server to come up...
set /a tries=0
:wait_loop
timeout /t 1 /nobreak >nul
curl -s -f http://localhost:%PORT%/api/devices >nul 2>&1
if not errorlevel 1 goto server_ready
set /a tries+=1
if !tries! lss %MAX_WAIT_S% goto wait_loop
echo.
echo [ERROR] Server didn't respond within %MAX_WAIT_S%s. Check the server window
echo for errors (most likely: port %PORT% already in use, or openmuscle install
echo broke^).
pause
exit /b 1
:server_ready
echo   Server up after !tries!s.

REM --- 6. adb reverse so Quest sees localhost --------------------------------
echo Forwarding port %PORT% to the Quest...
"%ADB%" reverse tcp:%PORT% tcp:%PORT%
if errorlevel 1 (
    echo [WARNING] adb reverse failed -- you may need to set it up manually.
)

REM --- 7. Launch Quest Browser to /vr ----------------------------------------
echo Opening Quest Browser to "%URL%"...
"%ADB%" shell am start -a android.intent.action.VIEW -d "%URL%" >nul

echo.
echo ============================================================
echo   OpenMuscle VR is ready  (arm=%ARM%, mode=%MODE%^)
echo   - Server runs in the "OpenMuscle VR Server" window
echo   - Put the headset on
echo   - Set both controllers down on a flat surface
echo   - Quest Browser should be open to /vr; tap "Enter VR"
echo.
echo   To stop: close the server window.
echo ============================================================
echo.
pause
