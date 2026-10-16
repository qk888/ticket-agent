@echo off
rem ============================================================
rem  One-click launcher for the ticket-agent demo.
rem
rem  This file is deliberately ASCII-ONLY. Chinese text lives in
rem  demo_menu.py, because .bat files are read in the console's
rem  ANSI codepage (GBK on a Chinese Windows) and would garble
rem  any UTF-8 Chinese written here. The console is switched to
rem  UTF-8 (65001) before Python prints, so Python's Chinese
rem  comes out intact.
rem
rem  Just double-click this file. No commands to remember.
rem
rem  Prefer Docker? Open a terminal here and run:
rem      docker compose up -d --build
rem  then open http://localhost:8000/  (Token: tok-acme)
rem  Details: Docker-Deployment.md
rem ============================================================

chcp 65001 >nul 2>&1
cd /d "%~dp0"

set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

rem Find a Python interpreter: prefer the py launcher, then python.
where py >nul 2>&1
if %errorlevel%==0 (
    py -3 demo_menu.py %*
    goto :done
)

where python >nul 2>&1
if %errorlevel%==0 (
    python demo_menu.py %*
    goto :done
)

echo.
echo   Python not found.
echo   Install Python 3.11+ from https://www.python.org/downloads/
echo   and tick "Add python.exe to PATH" during setup.
echo.
echo   Or use Docker instead (no Python needed):
echo     docker compose up -d --build
echo.

:done
echo.
pause
