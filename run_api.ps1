
# Trinity Bot - Run API Server

Write-Host "🚀 Starting Trinity Bot API Server..." -ForegroundColor Cyan
Write-Host ""
Write-Host "API will be available at: http://localhost:8000" -ForegroundColor Green
Write-Host "API Docs: http://localhost:8000/docs" -ForegroundColor Green
Write-Host ""

python -m uvicorn api.main:app --reload --host 0.0.0.0 --port 8000
