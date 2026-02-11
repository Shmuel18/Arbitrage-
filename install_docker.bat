@echo off
chcp 65001 >nul
echo Installing Docker Desktop...
echo This will take 5-10 minutes depending on your internet speed.
echo.

winget install -e --id Docker.DockerDesktop --silent --accept-source-agreements --accept-package-agreements

if %ERRORLEVEL% EQU 0 (
    echo.
    echo ============================================================
    echo Docker Desktop installed successfully!
    echo ============================================================
    echo.
    echo IMPORTANT: You need to RESTART your computer for Docker to work.
    echo.
    echo After restart, run: setup_docker.bat
    echo.
) else (
    echo.
    echo ============================================================
    echo Installation failed or cancelled
    echo ============================================================
    echo.
    echo Please try manual installation from:
    echo https://www.docker.com/products/docker-desktop/
    echo.
)

pause
