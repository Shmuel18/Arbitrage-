
# Trinity Bot - Start via main.py only (embedded API)

Set-Location 'c:\Users\shh92\Documents\Arbitrage'

Write-Host "⚠️ Standalone API mode disabled." -ForegroundColor Yellow
Write-Host "✅ Use main.py only (it already starts the embedded API on port 8000)." -ForegroundColor Green
Write-Host ""

& '.\venv\Scripts\python.exe' main.py
