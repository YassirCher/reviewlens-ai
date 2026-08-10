$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

if (-not (Test-Path ".env")) {
  Copy-Item ".env.example" ".env"
  Write-Host "Created .env from .env.example. Add your API keys before running analysis." -ForegroundColor Yellow
}

Write-Host "Setting up FastAPI backend..." -ForegroundColor Cyan
python -m venv "backend/.venv"
& "backend/.venv/Scripts/python.exe" -m pip install -r "backend/requirements.txt"

Write-Host "Setting up Next.js frontend..." -ForegroundColor Cyan
Push-Location "frontend"
npm install
Pop-Location

Write-Host "Setup complete." -ForegroundColor Green
Write-Host "Backend: .\\scripts\\run-backend.ps1"
Write-Host "Frontend: .\\scripts\\run-frontend.ps1"
