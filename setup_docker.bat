@echo off
echo ============================================================
echo Docker Desktop Installation - Waiting for completion
echo ============================================================
echo.

:CHECK_INSTALL
echo [%TIME%] Checking if installation is complete...
winget list --id Docker.DockerDesktop >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo [OK] Docker Desktop is installed!
    goto START_DOCKER
) else (
    echo [WAIT] Still installing... checking again in 10 seconds
    timeout /t 10 /nobreak >nul
    goto CHECK_INSTALL
)

:START_DOCKER
echo.
echo ============================================================
echo Starting Docker Desktop...
echo ============================================================
echo.
echo Please wait while Docker Desktop starts (this may take 1-2 minutes)...
echo.

start "" "C:\Program Files\Docker\Docker\Docker Desktop.exe"

echo.
echo ============================================================
echo Waiting for Docker to be ready...
echo ============================================================
echo.

:CHECK_DOCKER
timeout /t 5 /nobreak >nul
docker version >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo [OK] Docker is ready!
    goto START_REDIS
) else (
    echo [WAIT] Docker is starting... checking again in 5 seconds
    goto CHECK_DOCKER
)

:START_REDIS
echo.
echo ============================================================
echo Starting Redis container...
echo ============================================================
echo.

cd /d C:\Users\shh92\Documents\Arbitrage
docker-compose up -d redis

if %ERRORLEVEL% EQU 0 (
    echo.
    echo ============================================================
    echo [SUCCESS] Redis is running!
    echo ============================================================
    echo.
    docker ps
    echo.
    echo You can now run: python main.py
    echo.
) else (
    echo [ERROR] Failed to start Redis
    echo Please check docker-compose.yml
)

echo.
echo Press any key to exit...
pause >nul
