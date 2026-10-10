@echo off
REM ============================================================
REM   OpenMuscle launcher (Windows). The Desktop and Start-menu
REM   shortcut named "OpenMuscle" runs this file.
REM
REM   What it does:
REM     1. Finds the OpenMuscle program the installer set up (it does
REM        not need Python on PATH and does not activate anything).
REM     2. If OpenMuscle is already running in another window, just
REM        opens the page in your browser instead of starting a second
REM        copy (two copies would fight over the bands' data port).
REM     3. Otherwise runs "openmuscle web" in this window. It opens
REM        your browser by itself once the page is ready.
REM
REM   Any extra words after the file name are passed to "openmuscle web"
REM   (for example --no-browser). Normally there are none.
REM
REM   Closing this window stops OpenMuscle and frees its ports.
REM   If something goes wrong the window stays open so you can read it.
REM
REM   The installer keeps a copy of this file inside the app folder
REM   (next to the Scripts folder), so the shortcut keeps working even
REM   if you delete the folder you downloaded. Run from a downloaded
REM   folder instead, it looks in the default app folder.
REM ============================================================

setlocal EnableExtensions

REM A dry run (used by the CI smoke test) proves this file parses and
REM finds its way around without installing or starting anything.
if defined OPENMUSCLE_DRY_RUN (
    echo OpenMuscle launcher dry run: nothing started.
    exit /b 0
)
title OpenMuscle (leave open)

set "HERE=%~dp0"
set "EXE=%HERE%Scripts\openmuscle.exe"
if exist "%EXE%" goto installed_copy

REM Run from the downloaded folder: look in the default app folder.
set "ROOT=%OPENMUSCLE_HOME%"
if not defined ROOT set "ROOT=%USERPROFILE%\OpenMuscle"
set "EXE=%ROOT%\app\Scripts\openmuscle.exe"
goto have_exe

:installed_copy
REM Installed copy: the app folder sits directly inside the data folder.
for %%I in ("%HERE%..") do set "ROOT=%%~fI"
set "OPENMUSCLE_HOME=%ROOT%"

:have_exe
if not exist "%EXE%" (
    echo.
    echo OpenMuscle is not installed on this computer yet.
    echo Double-click Install-OpenMuscle.bat in the same folder as this file,
    echo wait for it to finish, then start OpenMuscle from the new shortcut.
    echo.
    echo Press any key to close this window.
    pause >nul
    exit /b 1
)

REM --- Already running? Then just open the page. ---------------------------
REM curl.exe ships with Windows 10 and 11. If it is missing the check is
REM skipped, and "openmuscle web" itself refuses to start a second copy.
REM The port the last run used is tried first (it is saved in logs\last-port.txt
REM in the data folder), then 8000 to 8010. Only a server that answers as
REM OpenMuscle counts; the address that gets opened is always printed.
set "RUNNING="
where curl >nul 2>nul
if not errorlevel 1 call :find_running
if defined RUNNING (
    echo OpenMuscle is already running at http://localhost:%RUNNING%
    echo Opening that address in your browser.
    start "" "http://localhost:%RUNNING%"
    timeout /t 3 /nobreak >nul
    exit /b 0
)

echo Starting OpenMuscle. Leave this window open while you work.
echo Close it, or press Ctrl+C, to stop.
echo.
"%EXE%" web %*
set "RC=%ERRORLEVEL%"
if "%RC%"=="0" exit /b 0

echo.
echo OpenMuscle stopped with a problem. The message above says what went wrong.
echo Run Install-OpenMuscle.bat again to repair the install, or look at
echo "%ROOT%\logs\install.log" if the install itself had trouble.
echo.
echo Press any key to close this window.
pause >nul
exit /b %RC%

:probe
if defined RUNNING exit /b 0
curl -s --max-time 2 "http://127.0.0.1:%1/api/mode" 2>nul | findstr /c:"openmuscle" >nul && set "RUNNING=%1"
exit /b 0

:find_running
set "LASTPORT="
if exist "%ROOT%\logs\last-port.txt" set /p LASTPORT=<"%ROOT%\logs\last-port.txt"
REM Keep it only if it is all digits (a damaged file must not reach the URL).
if defined LASTPORT for /f "delims=0123456789" %%X in ("%LASTPORT%") do set "LASTPORT="
if defined LASTPORT call :probe %LASTPORT%
for /l %%P in (8000,1,8010) do call :probe %%P
exit /b 0
