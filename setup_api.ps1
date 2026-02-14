# Trinity Bot - Setup Scripts

Write-Host "🚀 Trinity Bot - API Setup" -ForegroundColor Cyan
Write-Host ""

# Install API dependencies
Write-Host "📦 Installing API dependencies..." -ForegroundColor Yellow
pip install -r api/requirements.txt

if ($LASTEXITCODE -eq 0) {
    Write-Host "✅ API dependencies installed successfully!" -ForegroundColor Green
} else {
    Write-Host "❌ Failed to install API dependencies" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "🎉 Setup complete!" -ForegroundColor Green
Write-Host "Run './run_api.ps1' to start the API server" -ForegroundColor Cyan
