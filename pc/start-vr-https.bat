@echo off
REM ============================================================
REM   OpenMuscle VR launcher -- HTTPS / LAN / cordless
REM
REM   Starts `openmuscle web` with TLS so the Quest can reach it
REM   over Wi-Fi from anywhere in your house (no USB cable, no
REM   `adb reverse` needed). Requires the one-time mkcert setup
REM   first -- see docs/vr-setup.md or the OpenMuscle-AR wiki
REM   "Cordless Setup mkcert HTTPS" page if you haven't done it.
REM
REM   Sibling to start-vr.bat (which does the USB+adb-reverse +
REM   plain HTTP path). Use whichever fits the situation:
REM     start-vr.bat            tethered, fast iteration, no certs
REM     start-vr-https.bat      untethered, real field-capture use
REM
REM   Usage:  start-vr-https.bat
REM   Stop:   Ctrl-C in this window.
REM ============================================================

setlocal

REM REPO is this script's own directory (i.e. pc/), so the launcher works
REM from any clone path without editing -- no hardcoded paths.
set "REPO=%~dp0"
if "%REPO:~-1%"=="\" set "REPO=%REPO:~0,-1%"
set "PORT=8000"
set "CERT=%REPO%\vr-cert.pem"
set "KEY=%REPO%\vr-key.pem"

REM --- 1. Verify openmuscle CLI is installed ---
where openmuscle >nul 2>nul
if errorlevel 1 (
    echo [ERROR] openmuscle CLI not found on PATH.
    echo From the repo root: cd pc ^&^& pip install -e .
    pause
    exit /b 1
)

REM --- 2. Verify the cert files exist ---
if not exist "%CERT%" goto missing_certs
if not exist "%KEY%"  goto missing_certs
goto have_certs

:missing_certs
echo [ERROR] TLS cert files not found in %REPO%:
echo   vr-cert.pem  expected at: %CERT%
echo   vr-key.pem   expected at: %KEY%
echo.
echo You need to run the one-time mkcert setup. See:
echo   https://github.com/Open-Muscle/OpenMuscle-AR/wiki/Cordless-Setup-mkcert-HTTPS
echo.
echo Or for now use the USB-tethered path with plain HTTP:
echo   start-vr.bat
pause
exit /b 1

:have_certs

REM --- 3. Sniff a LAN IP so we can print the headset URL ---
REM Uses PowerShell to pick the first non-loopback, non-link-local IPv4 address.
REM Quotes / pipes inside the embedded PS command need ^| to survive cmd parsing.
set "LAN_IP="
for /f "tokens=*" %%a in ('powershell -NoProfile -Command "(Get-NetIPAddress -AddressFamily IPv4 ^| Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' -and $_.PrefixOrigin -ne 'WellKnown' } ^| Select-Object -First 1).IPAddress"') do set "LAN_IP=%%a"
if "%LAN_IP%"=="" set "LAN_IP=<your-LAN-IP>"

REM --- 4. Banner with the URLs to open on the headset ---
echo.
echo ============================================================
echo   OpenMuscle VR  --  HTTPS / LAN (untethered)
echo ============================================================
echo.
echo Open in Quest Browser:
echo.
echo   https://%LAN_IP%:%PORT%/vr            (VR mode)
echo   https://%LAN_IP%:%PORT%/vr?mode=ar    (AR / passthrough mode)
echo   https://%LAN_IP%:%PORT%/                (desktop Studio UI)
echo.
echo (left arm? add ^&arm=left to either /vr URL)
echo.
echo Make sure the Quest has the mkcert root CA installed, otherwise
echo the browser will warn "Your connection is not private" and refuse
echo to enable WebXR. See the Cordless Setup wiki page.
echo.
echo Ctrl-C in this window stops the server.
echo ============================================================
echo.

cd /d "%REPO%"
openmuscle web --ssl-certfile vr-cert.pem --ssl-keyfile vr-key.pem
