@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Wangwang Team Saves the Day

where python >nul 2>nul
if errorlevel 1 (
    echo.
    echo [x] Python not found in PATH.
    echo     Install Python first, and tick "Add Python to PATH".
    echo.
    pause
    exit /b 1
)

echo.
echo   Starting local server. Close this window to stop.
echo.
python "app\server.py"

echo.
echo   Server stopped.
pause
