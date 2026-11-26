@echo off
rem Galaxian launcher for Windows - double-click to play.
rem Extra options can be added after the name, e.g.  run_galaxian.bat --scale 4
setlocal
cd /d "%~dp0"

set "PY="
py -3 --version >nul 2>&1 && set "PY=py -3"
if not defined PY python --version >nul 2>&1 && set "PY=python"
if not defined PY python3 --version >nul 2>&1 && set "PY=python3"

if not defined PY (
    echo [galaxian] PROBLEM: Python was not found.
    echo.
    echo   Install Python 3 from https://www.python.org/downloads/
    echo   and tick "Add python.exe to PATH" during setup, then run this again.
    echo.
    pause
    exit /b 1
)

%PY% launcher.py %*
if errorlevel 1 (
    echo.
    pause
)
endlocal
