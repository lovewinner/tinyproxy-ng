@echo off
REM Quick start script for tinyproxy-ng

echo ================================================
echo   Tinyproxy-ng Quick Start
echo ================================================
echo.

REM Check if Docker is installed
docker --version >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo [OK] Docker detected
    set USE_DOCKER=true
) else (
    echo [INFO] Docker not found, will use native installation
    set USE_DOCKER=false
)

if "%USE_DOCKER%"=="true" (
    echo.
    echo Starting with Docker...
    echo.
    
    REM Check if .env exists
    if not exist .env (
        echo Creating .env from .env.example...
        copy .env.example .env >nul
        echo.
        echo [WARNING] Please edit .env file with your settings
        echo    Edit with: notepad .env
        echo.
        pause
    )
    
    REM Build and run
    echo Building Docker image...
    docker-compose build
    if %ERRORLEVEL% NEQ 0 (
        echo [ERROR] Docker build failed
        pause
        exit /b 1
    )
    
    echo Starting container...
    docker-compose up -d
    if %ERRORLEVEL% NEQ 0 (
        echo [ERROR] Docker start failed
        pause
        exit /b 1
    )
    
    echo.
    echo [SUCCESS] Container started!
    echo.
    echo Useful commands:
    echo   View logs:     docker-compose logs -f
    echo   Stop service:  docker-compose down
    echo   Restart:       docker-compose restart
    echo.
    echo Proxy is now running on port 26128
    echo Test with: curl -x http://username:password@localhost:26128 https://example.com
    
) else (
    echo.
    echo Starting native installation...
    echo.
    
    REM Check Python
    python --version >nul 2>&1
    if %ERRORLEVEL% NEQ 0 (
        echo [ERROR] Python not found. Please install Python 3.8+
        pause
        exit /b 1
    )
    
    REM Install dependencies
    echo Installing dependencies...
    pip install -r requirements.txt
    if %ERRORLEVEL% NEQ 0 (
        echo [ERROR] Failed to install dependencies
        pause
        exit /b 1
    )
    
    REM Check config
    if not exist config.yaml (
        echo Creating config.yaml from config.example.yaml...
        copy config.example.yaml config.yaml >nul
        echo.
        echo [WARNING] Please edit config.yaml with your settings
        echo    Edit with: notepad config.yaml
        echo.
        pause
    )
    
    echo.
    echo Starting proxy server...
    echo Press Ctrl+C to stop
    echo.
    python proxy_server.py
)

pause
