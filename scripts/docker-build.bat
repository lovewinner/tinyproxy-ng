@echo off
REM Build Docker image for tinyproxy-ng

set IMAGE_NAME=tinyproxy-ng
set IMAGE_TAG=latest

echo Building Docker image: %IMAGE_NAME%:%IMAGE_TAG%
docker build -t %IMAGE_NAME%:%IMAGE_TAG% .

if %ERRORLEVEL% EQU 0 (
    echo.
    echo Build successful!
    echo.
    echo To run the container:
    echo   docker run -d -p 26128:26128 --name tinyproxy-ng %IMAGE_NAME%:%IMAGE_TAG%
    echo.
    echo Or use docker-compose:
    echo   docker-compose up -d
) else (
    echo.
    echo Build failed! Check the error messages above.
)

pause
