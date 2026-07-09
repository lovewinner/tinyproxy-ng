@echo off
chcp 65001 >nul
title HTTP/HTTPS Proxy Server
color 0A
cd /d "%~dp0"
cls

echo ========================================
echo    HTTP/HTTPS Proxy Server - Windows
echo ========================================
echo.

REM Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found! Please install Python 3.8+
    echo Download: https://www.python.org/downloads/
    pause
    exit /b 1
)

REM Check dependencies
echo [1/3] Checking dependencies...
pip show aiohttp >nul 2>&1
if errorlevel 1 (
    echo Installing dependencies...
    pip install -r ..\requirements.txt
    if errorlevel 1 (
        echo [ERROR] Dependency installation failed, check network connection
        pause
        exit /b 1
    )
    echo Dependencies installed!
) else (
    echo Dependencies OK.
)

REM Check config file
echo [2/3] Checking config file...
if not exist "..\config.yaml" (
    echo [WARN] config.yaml not found, using default configuration
    echo Copy ..\config.example.yaml to config.yaml and edit it
)

REM Start server
echo [3/3] Starting proxy server...
echo.
echo ========================================
echo Server is running...
echo Listen: 0.0.0.0:8080
echo Auth: Enabled
echo ========================================
echo.
echo Press Ctrl+C to stop the server
echo.
echo Log output:
echo.

python ..\proxy_server.py

if errorlevel 1 (
    echo.
    echo [ERROR] Server exited abnormally
    pause
)