# ============================================================
#  Trinity Bot — Full Reset Script
#  Clears ALL data: Redis, logs, journal, pycache, pip cache
#  Run from project root:  .\full_reset.ps1
# ============================================================

Write-Host ""
Write-Host "========================================" -ForegroundColor Red
Write-Host "   TRINITY BOT — FULL SYSTEM RESET"      -ForegroundColor Red
Write-Host "========================================" -ForegroundColor Red
Write-Host ""
Write-Host "This will DELETE:" -ForegroundColor Yellow
Write-Host "  - All Redis keys (trinity:*)"
Write-Host "  - All log files (logs/*.log, logs/*.jsonl)"
Write-Host "  - Trade journal (logs/trade_journal.jsonl)"
Write-Host "  - Python cache (__pycache__)"
Write-Host "  - Pytest cache (.pytest_cache)"
Write-Host ""

$confirm = Read-Host "Type 'yes' to confirm full reset"
if ($confirm -ne 'yes') {
    Write-Host "`n[-] Aborted." -ForegroundColor Gray
    exit
}

Write-Host ""

# ── 1. Stop any running bot processes ─────────────────────────
Write-Host "[1/5] Stopping any running Python bot processes..." -ForegroundColor Cyan
$procs = Get-Process -Name python -ErrorAction SilentlyContinue |
         Where-Object { $_.Path -like "*Arbitrage*" }
if ($procs) {
    $procs | Stop-Process -Force -ErrorAction SilentlyContinue
    Write-Host "      Stopped $($procs.Count) process(es)" -ForegroundColor Green
    Start-Sleep -Seconds 2
} else {
    Write-Host "      No bot processes running" -ForegroundColor Gray
}

# ── 2. Clear Redis ────────────────────────────────────────────
Write-Host "[2/5] Clearing Redis data..." -ForegroundColor Cyan
$redisScript = @"
import asyncio, os
from dotenv import load_dotenv
load_dotenv()
import redis.asyncio as aioredis
async def go():
    try:
        r = await aioredis.from_url(os.getenv('REDIS_URL', 'redis://localhost:6379/0'))
        keys = await r.keys('trinity:*')
        if keys:
            deleted = await r.delete(*keys)
            print(f'      Deleted {deleted} Redis keys')
        else:
            print('      Redis already clean (0 keys)')
        await r.aclose()
    except Exception as e:
        print(f'      Redis error: {e}')
        print('      (Is Redis running? If not, skip this step)')
asyncio.run(go())
"@
$redisScript | & .\venv\Scripts\python.exe -

# ── 3. Clear log files ────────────────────────────────────────
Write-Host "[3/5] Clearing log files..." -ForegroundColor Cyan
$logsDir = Join-Path $PSScriptRoot "logs"
if (Test-Path $logsDir) {
    $logFiles = Get-ChildItem -Path $logsDir -File -Recurse
    foreach ($f in $logFiles) {
        Remove-Item -Path $f.FullName -Force -ErrorAction SilentlyContinue
        Write-Host "      Deleted: $($f.Name)" -ForegroundColor DarkGray
    }
    if (-not $logFiles) {
        Write-Host "      No log files found" -ForegroundColor Gray
    }
} else {
    New-Item -ItemType Directory -Path $logsDir -Force | Out-Null
    Write-Host "      Created empty logs/ directory" -ForegroundColor Gray
}

# ── 4. Clear Python caches ───────────────────────────────────
Write-Host "[4/5] Clearing Python caches..." -ForegroundColor Cyan
$cacheCount = 0
Get-ChildItem -Path $PSScriptRoot -Directory -Recurse -Filter "__pycache__" | ForEach-Object {
    Remove-Item -Path $_.FullName -Recurse -Force -ErrorAction SilentlyContinue
    $cacheCount++
}
$pytestCache = Join-Path $PSScriptRoot ".pytest_cache"
if (Test-Path $pytestCache) {
    Remove-Item -Path $pytestCache -Recurse -Force -ErrorAction SilentlyContinue
    $cacheCount++
}
Write-Host "      Removed $cacheCount cache directories" -ForegroundColor Green

# ── 5. Pull latest code from GitHub ──────────────────────────
Write-Host "[5/5] Pulling latest code from GitHub..." -ForegroundColor Cyan
try {
    $pullOutput = git pull 2>&1 | Out-String
    Write-Host "      $($pullOutput.Trim())" -ForegroundColor Green
} catch {
    Write-Host "      git pull failed: $_" -ForegroundColor Yellow
}

# ── Done ──────────────────────────────────────────────────────
Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "   RESET COMPLETE — READY TO START"      -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "To start the bot:" -ForegroundColor White
Write-Host "  .\venv\Scripts\python.exe main.py" -ForegroundColor Yellow
Write-Host ""
