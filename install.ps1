# Trinity Bot - Automated Setup Script
# Run this script to setup everything automatically

Write-Host "================================" -ForegroundColor Cyan
Write-Host "  Trinity Bot - Setup Wizard   " -ForegroundColor Cyan
Write-Host "================================" -ForegroundColor Cyan
Write-Host ""

# Check if running as Administrator
$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "⚠️  Warning: Not running as Administrator. Some operations might fail." -ForegroundColor Yellow
    Write-Host ""
}

# Function to check if command exists
function Test-Command {
    param([string]$Command)
    try {
        if (Get-Command $Command -ErrorAction Stop) { return $true }
    }
    catch { return $false }
}

# Step 1: Check Prerequisites
Write-Host "Step 1: Checking Prerequisites..." -ForegroundColor Green

if (Test-Command python) {
    $pythonVersion = (python --version 2>&1)
    Write-Host "✅ Python: $pythonVersion" -ForegroundColor Green
}
else {
    Write-Host "❌ Python not found! Download from: https://www.python.org/downloads/" -ForegroundColor Red
    exit 1
}

if (Test-Command node) {
    $nodeVersion = (node --version)
    Write-Host "✅ Node.js: $nodeVersion" -ForegroundColor Green
}
else {
    Write-Host "❌ Node.js not found! Download from: https://nodejs.org/" -ForegroundColor Red
    exit 1
}

if (Test-Command docker) {
    $dockerVersion = (docker --version)
    Write-Host "✅ Docker: $dockerVersion" -ForegroundColor Green
}
else {
    Write-Host "⚠️  Docker not found. You'll need to install Redis manually." -ForegroundColor Yellow
}

Write-Host ""

# Step 2: Setup Python Virtual Environment
Write-Host "Step 2: Setting up Python Virtual Environment..." -ForegroundColor Green

if (Test-Path "venv") {
    Write-Host "Virtual environment already exists." -ForegroundColor Yellow
}
else {
    Write-Host "Creating virtual environment..."
    python -m venv venv
    if ($LASTEXITCODE -ne 0) {
        Write-Host "❌ Failed to create virtual environment!" -ForegroundColor Red
        exit 1
    }
    Write-Host "✅ Virtual environment created" -ForegroundColor Green
}

Write-Host ""

# Step 3: Install Python Dependencies
Write-Host "Step 3: Installing Python Dependencies..." -ForegroundColor Green

# Activate virtual environment
& ".\venv\Scripts\Activate.ps1"

# Upgrade pip
Write-Host "Upgrading pip..."
python -m pip install --upgrade pip --quiet

# Install requirements
Write-Host "Installing packages from requirements.txt..."
pip install -r requirements.txt --quiet

if ($LASTEXITCODE -eq 0) {
    Write-Host "✅ Python dependencies installed" -ForegroundColor Green
}
else {
    Write-Host "❌ Failed to install Python dependencies!" -ForegroundColor Red
    exit 1
}

Write-Host ""

# Step 4: Install Frontend Dependencies
Write-Host "Step 4: Installing Frontend Dependencies..." -ForegroundColor Green

if (Test-Path "frontend") {
    Push-Location frontend
    
    Write-Host "Installing npm packages..."
    npm install --silent
    
    if ($LASTEXITCODE -eq 0) {
        Write-Host "✅ Frontend dependencies installed" -ForegroundColor Green
    }
    else {
        Write-Host "❌ Failed to install frontend dependencies!" -ForegroundColor Red
        Pop-Location
        exit 1
    }
    
    Pop-Location
}
else {
    Write-Host "⚠️  Frontend directory not found. Skipping..." -ForegroundColor Yellow
}

Write-Host ""

# Step 5: Start Redis
Write-Host "Step 5: Starting Redis Database..." -ForegroundColor Green

if (Test-Command docker-compose) {
    Write-Host "Starting Redis with Docker Compose..."
    docker-compose up -d redis
    
    if ($LASTEXITCODE -eq 0) {
        Start-Sleep -Seconds 3
        Write-Host "✅ Redis started successfully" -ForegroundColor Green
    }
    else {
        Write-Host "❌ Failed to start Redis!" -ForegroundColor Red
    }
}
else {
    Write-Host "⚠️  Docker Compose not found. Please start Redis manually." -ForegroundColor Yellow
}

Write-Host ""

# Step 6: Check .env file
Write-Host "Step 6: Checking Configuration Files..." -ForegroundColor Green

if (Test-Path ".env") {
    Write-Host "✅ .env file exists" -ForegroundColor Green
}
else {
    Write-Host "⚠️  .env file not found!" -ForegroundColor Yellow
    Write-Host "Creating template .env file..."
    
    $envTemplate = @"
# Trinity Bot - Environment Configuration

# OKX Exchange
OKX_API_KEY=your_okx_api_key_here
OKX_API_SECRET=your_okx_secret_here
OKX_API_PASSPHRASE=your_okx_passphrase_here

# Bybit Exchange
BYBIT_API_KEY=your_bybit_api_key_here
BYBIT_API_SECRET=your_bybit_secret_here

# Binance Exchange
BINANCE_API_KEY=your_binance_api_key_here
BINANCE_API_SECRET=your_binance_secret_here

# Gate.io Exchange
GATEIO_API_KEY=your_gateio_api_key_here
GATEIO_API_SECRET=your_gateio_secret_here

# KuCoin Exchange
KUCOIN_API_KEY=your_kucoin_api_key_here
KUCOIN_API_SECRET=your_kucoin_secret_here
KUCOIN_API_PASSPHRASE=your_kucoin_passphrase_here
"@
    
    $envTemplate | Out-File -FilePath ".env" -Encoding UTF8
    Write-Host "📝 Template .env created. Please fill in your API keys!" -ForegroundColor Cyan
}

if (Test-Path "config.yaml") {
    Write-Host "✅ config.yaml exists" -ForegroundColor Green
}
else {
    Write-Host "❌ config.yaml not found!" -ForegroundColor Red
}

Write-Host ""

# Step 7: Run Tests
Write-Host "Step 7: Running Tests..." -ForegroundColor Green

pytest tests/ -v --tb=short 2>&1 | Out-Null

if ($LASTEXITCODE -eq 0) {
    Write-Host "✅ All tests passed" -ForegroundColor Green
}
else {
    Write-Host "⚠️  Some tests failed. Check pytest output for details." -ForegroundColor Yellow
}

Write-Host ""

# Final Summary
Write-Host "================================" -ForegroundColor Cyan
Write-Host "      Setup Complete! 🎉       " -ForegroundColor Cyan
Write-Host "================================" -ForegroundColor Cyan
Write-Host ""

Write-Host "Next Steps:" -ForegroundColor Yellow
Write-Host "1. Edit .env file and add your API keys" -ForegroundColor White
Write-Host "2. Review config.yaml settings" -ForegroundColor White
Write-Host "3. Start the backend:  .\run.ps1  or  python main.py" -ForegroundColor White
Write-Host "4. Start the frontend: cd frontend; npm start" -ForegroundColor White
Write-Host ""

Write-Host "Quick Start Commands:" -ForegroundColor Yellow
Write-Host "  Backend:  .\venv\Scripts\Activate.ps1; python main.py" -ForegroundColor Cyan
Write-Host "  Frontend: cd frontend; npm start" -ForegroundColor Cyan
Write-Host "  Tests:    pytest tests/ -v" -ForegroundColor Cyan
Write-Host ""

Write-Host "Dashboard will be available at: http://localhost:3000" -ForegroundColor Green
Write-Host ""

# Keep console open
Write-Host "Press any key to exit..."
$null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
