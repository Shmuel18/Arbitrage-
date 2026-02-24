# Reset Trinity Bot History
# This script clears all Redis data and log files to start from scratch.
# Run from project root: .\reset_history.ps1

Write-Host "[!] WARNING: This will delete ALL trade history, PnL data, and logs." -ForegroundColor Yellow
$confirm = Read-Host "Are you sure you want to proceed? (y/n)"
if ($confirm -ne 'y') {
    Write-Host "[-] Reset aborted."
    exit
}

Write-Host "`n1. Clearing Redis data..." -ForegroundColor Cyan
# Run the python script to clear redis keys
& .\venv\Scripts\python.exe scripts/clear_redis.py

Write-Host "`n2. Clearing log files..." -ForegroundColor Cyan
& .\scripts\clear_logs.ps1

Write-Host "`nDone! System Reset Complete! You can now start the bot fresh." -ForegroundColor Green
