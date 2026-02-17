@echo off
cd /d c:\Users\shh92\Documents\Arbitrage

echo ========================================
echo    Starting Trinity v3.0.0 (Full Stack)
echo ========================================
echo.

:: Start frontend in background (port 3000)
echo [1/2] Starting Frontend (port 3000)...
cd frontend
start "Trinity Frontend" cmd /c "npm start"
cd ..

:: Give frontend a moment to start
timeout /t 3 /nobreak >nul

:: Start Python bot + embedded API server (foreground - will keep window open)
echo [2/2] Starting Trinity Bot + API Server (port 8000)...
echo.
c:\Users\shh92\Documents\Arbitrage\venv\Scripts\python.exe main.py
