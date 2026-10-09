@echo off
REM ============================================================
REM   Double-click this file to install OpenMuscle on Windows.
REM   It runs install.ps1 in this folder. Nothing here needs
REM   administrator rights, and the PowerShell policy is bypassed
REM   for this one script only (nothing on your computer changes).
REM ============================================================
setlocal
title Install OpenMuscle
set "HERE=%~dp0"
if not exist "%HERE%install.ps1" (
    echo install.ps1 was not found next to this file.
    echo Extract the whole downloaded ZIP first, then try again.
    echo.
    echo Press any key to close this window.
    pause >nul
    exit /b 1
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%HERE%install.ps1" %*
set "RC=%ERRORLEVEL%"
echo.
echo Press any key to close this window.
pause >nul
exit /b %RC%
