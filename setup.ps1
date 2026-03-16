# Trinity Bot - Complete Setup

Write-Host "🚀 Trinity Bot - Complete Setup" -ForegroundColor Cyan
Write-Host "=================================" -ForegroundColor Cyan
Write-Host ""

# Setup API
Write-Host "Step 1/2: Setting up API..." -ForegroundColor Yellow
.\setup_api.ps1

if ($LASTEXITCODE -ne 0) {
    Write-Host "❌ API setup failed!" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "=================================" -ForegroundColor Cyan
Write-Host ""

# Setup Frontend
Write-Host "Step 2/2: Setting up Frontend..." -ForegroundColor Yellow
.\setup_frontend.ps1

if ($LASTEXITCODE -ne 0) {
    Write-Host "❌ Frontend setup failed!" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "=================================" -ForegroundColor Cyan
Write-Host "🎉 Complete setup finished!" -ForegroundColor Green
Write-Host ""
Write-Host "To start the full system:" -ForegroundColor Cyan
Write-Host "1. Start Redis (if not running)" -ForegroundColor White
Write-Host "2. Run: .\run_frontend.ps1 (in one terminal)" -ForegroundColor White
Write-Host "3. Run: .\run.ps1 (in another terminal — starts bot + embedded API)" -ForegroundColor White
Write-Host ""
Write-Host "Then open: http://localhost:3000" -ForegroundColor Green
Write-Host ""
