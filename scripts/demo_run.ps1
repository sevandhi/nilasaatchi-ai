# Start the whole application on Windows: database (Docker) -> API (:8000) -> web UI (:5173), then open
# the browser. Easiest: double-click START-WINDOWS.cmd. Press Enter in this window to stop the API and UI
# (the database container keeps running; stop it with: docker compose stop db).
$ErrorActionPreference = "Continue"   # exit codes are checked explicitly; "Stop" would abort on native stderr in PowerShell 5.1
Set-Location (Split-Path -Parent $PSScriptRoot)
$env:UV_PROJECT_ENVIRONMENT = Join-Path (Get-Location).Path ".venv-win"   # same env as SETUP-WINDOWS
$env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
            [Environment]::GetEnvironmentVariable("Path", "User") + ";" + "$env:USERPROFILE\.local\bin"
$Log = "data\logs"; New-Item -ItemType Directory -Force -Path $Log | Out-Null

function Say($m) { Write-Host ""; Write-Host "==> $m" -ForegroundColor Green }
function Listening($port) { return [bool](Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) }
function Wait-Http($url, $name, $seconds) {
  for ($i = 0; $i -lt $seconds; $i++) {
    try { Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop | Out-Null; return } catch { Start-Sleep -Seconds 1 }
  }
  Write-Host "!! $name did not start in $seconds s; see $Log\" -ForegroundColor Red
  Read-Host "Press Enter to close"; exit 1
}

docker info *> $null
if ($LASTEXITCODE -ne 0) { Write-Host "!! Start Docker Desktop first, then run this again." -ForegroundColor Red; Read-Host "Press Enter to close"; exit 1 }

Say "1/3 database (PostGIS, port 5439)"
docker compose up -d db | Out-Null
for ($i = 0; $i -lt 60; $i++) {
  docker compose exec -T db pg_isready -U nila -d nilasaatchi *> $null
  if ($LASTEXITCODE -eq 0) { break }
  Start-Sleep -Seconds 1
}

$procs = @()
Say "2/3 API (http://localhost:8000)"
if (Listening 8000) { Write-Host "port 8000 already in use: assuming the API is already running" }
else {
  $procs += Start-Process -FilePath "uv" -ArgumentList "run", "uvicorn", "app.api.main:app", "--port", "8000" `
    -RedirectStandardOutput "$Log\api.log" -RedirectStandardError "$Log\api.err.log" -WindowStyle Hidden -PassThru
}
Wait-Http "http://localhost:8000/health" "API" 120

Say "3/3 web UI (http://localhost:5173)"
if (Listening 5173) { Write-Host "port 5173 already in use: assuming the UI is already running" }
else {
  $procs += Start-Process -FilePath "npm.cmd" -ArgumentList "run", "dev", "--", "--port", "5173", "--strictPort" `
    -WorkingDirectory "web" -RedirectStandardOutput "$Log\web.log" -RedirectStandardError "$Log\web.err.log" -WindowStyle Hidden -PassThru
}
Wait-Http "http://localhost:5173" "web UI" 120

Say "NilaSaatchi AI is running:  http://localhost:5173   (API docs: http://localhost:8000/docs)"
Start-Process "http://localhost:5173"
Read-Host "Press Enter to stop the API and UI"
foreach ($p in $procs) { if ($p -and -not $p.HasExited) { taskkill /PID $p.Id /T /F | Out-Null } }
Write-Host "Stopped. (Database still running; stop it with: docker compose stop db)"
