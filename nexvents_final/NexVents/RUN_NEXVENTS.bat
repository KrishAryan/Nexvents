@echo off
setlocal
cd /d "%~dp0"
title NexVents - Smart Event Management System

echo.
echo  ======================================
echo       N E X V E N T S
echo   Campus Event Management System
echo  ======================================
echo.

if not exist ".venv\Scripts\python.exe" (
    echo [1/3] Creating Python virtual environment...
    where py >nul 2>nul
    if errorlevel 1 (
        python -m venv .venv
    ) else (
        py -3 -m venv .venv
    )
    if errorlevel 1 (
        echo Could not create virtual environment. Install Python 3.10+ first.
        pause
        exit /b 1
    )
)

echo [2/3] Installing project requirements...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo Package installation failed. Check your internet connection and try again.
    pause
    exit /b 1
)

echo [3/3] Starting NexVents...
echo Open http://127.0.0.1:5000 in your browser.
echo Press Ctrl+C to stop the server.
echo.
".venv\Scripts\python.exe" run.py
pause
