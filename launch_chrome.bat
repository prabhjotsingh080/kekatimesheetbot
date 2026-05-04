@echo off
REM ============================================================
REM  Launch Chrome with Remote Debugging Port 9222
REM  Run this BEFORE using the Keka automation tool
REM ============================================================

echo  Launching Chrome with remote debugging on port 9222...
echo  After Chrome opens:
echo    1. Log in to https://cloudsufi.keka.com
echo    2. Run the automation tool in another terminal
echo.

REM Try default Chrome locations
set CHROME1=C:\Program Files\Google\Chrome\Application\chrome.exe
set CHROME2=C:\Program Files (x86)\Google\Chrome\Application\chrome.exe
set CHROME3=%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe
set USERDIR=C:\ChromeDebugProfile

if exist "%CHROME1%" (
    start "" "%CHROME1%" --remote-debugging-port=9222 --user-data-dir="%USERDIR%"
    goto :launched
)
if exist "%CHROME2%" (
    start "" "%CHROME2%" --remote-debugging-port=9222 --user-data-dir="%USERDIR%"
    goto :launched
)
if exist "%CHROME3%" (
    start "" "%CHROME3%" --remote-debugging-port=9222 --user-data-dir="%USERDIR%"
    goto :launched
)

echo [ERROR] Chrome not found in default locations.
echo         Edit this file and set the correct CHROME path.
pause
exit /b 1

:launched
echo  Chrome launched! Waiting for it to open...
timeout /t 3 /nobreak >nul
echo  Chrome is ready. Log in to Keka, then run:
echo    python cli.py fill --input "your timesheet description"
echo.
