# One command local dev on Windows: FastAPI on :8000 + Vite on :5173 (Ctrl+C stops both).
#   powershell -ExecutionPolicy Bypass -File scripts\dev.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not (Test-Path "backend\.venv")) {
    Write-Host "==> Creating backend\.venv"
    python -m venv backend\.venv
}
$py = Join-Path $root "backend\.venv\Scripts\python.exe"

& $py -c "import fastapi" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "==> Installing backend dependencies"
    & $py -m pip install -q -e "backend[dev]"
}
if (-not (Test-Path "frontend\node_modules")) {
    Write-Host "==> Installing frontend dependencies"
    Push-Location frontend; npm ci --no-audit --no-fund; Pop-Location
}

Write-Host "==> API  http://127.0.0.1:8000  (docs: /docs)"
$api = Start-Process -FilePath $py -ArgumentList "-m", "uvicorn", "app.main:app", "--reload", "--port", "8000" `
    -WorkingDirectory (Join-Path $root "backend") -PassThru -NoNewWindow
try {
    Write-Host "==> UI   http://localhost:5173"
    Push-Location frontend
    npm run dev
} finally {
    Pop-Location
    # Stop only the API process tree we started.
    if (-not $api.HasExited) { taskkill /PID $api.Id /T /F | Out-Null }
}
