@echo off
REM ============================================================
REM  Keka Automation — Windows Setup Script
REM  Run this once to set up the environment
REM ============================================================

echo.
echo  Keka Timesheet Automation — Setup
echo  ==================================
echo.

REM Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Install Python 3.10+ from https://python.org
    pause
    exit /b 1
)

REM Try uv first
uv --version >nul 2>&1
if not errorlevel 1 (
    echo [INFO] uv found — using uv
    uv venv .venv
    call .venv\Scripts\activate.bat
    uv pip install -r requirements.txt
) else (
    echo [INFO] uv not found — using pip
    python -m venv .venv
    call .venv\Scripts\activate.bat
    pip install -r requirements.txt
)

REM Install Playwright browsers
echo.
echo [INFO] Installing Playwright Chromium...
playwright install chromium

REM Copy env file
if not exist .env (
    copy .env.example .env
    echo [INFO] Created .env — please add your GEMINI_API_KEY
)

echo.
echo  Setup complete!
echo  ---------------
echo  1. Edit .env and add your GEMINI_API_KEY
echo  2. Launch Chrome:  launch_chrome.bat
echo  3. Log in to Keka at https://cloudsufi.keka.com
echo  4. Run:  python cli.py fill --input "Worked on ProjectX Mon-Wed 8h"
echo.
pause
