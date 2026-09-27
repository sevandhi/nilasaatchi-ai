# One-time setup on Windows 10/11 (native PowerShell, no WSL needed), run from the unpacked demo folder.
# Easiest: double-click SETUP-WINDOWS.cmd. Safe to re-run.
# Installs uv + Python 3.12 and Node.js LTS (winget) if missing, the project packages, the database and
# OCR images; restores the database dump and the data bundle; creates .env.
$ErrorActionPreference = "Continue"   # exit codes are checked explicitly; "Stop" would abort on native stderr in PowerShell 5.1
Set-Location (Split-Path -Parent $PSScriptRoot)
$Root = (Get-Location).Path

function Say($m) { Write-Host ""; Write-Host "==> $m" -ForegroundColor Green }
function Fail($m) { Write-Host ""; Write-Host "!! $m" -ForegroundColor Red; exit 1 }
function Refresh-Path {
  $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
              [Environment]::GetEnvironmentVariable("Path", "User") + ";" + "$env:USERPROFILE\.local\bin"
}
function Run($exe, [string[]]$argv) {
  & $exe @argv
  if ($LASTEXITCODE -ne 0) { Fail "command failed ($LASTEXITCODE): $exe $($argv -join ' ')" }
}
function Has($cmd) { return [bool](Get-Command $cmd -ErrorAction SilentlyContinue) }
Refresh-Path

Say "Checking Docker Desktop"
if (-not (Has "docker")) { Fail "Docker Desktop is required: install it from https://www.docker.com/products/docker-desktop/ , start it, then run this again." }
docker info *> $null
if ($LASTEXITCODE -ne 0) { Fail "Docker Desktop is installed but not running. Start Docker Desktop (whale icon in the tray says 'running'), then run this again." }
docker compose version *> $null
if ($LASTEXITCODE -ne 0) { Fail "Docker Compose v2 is missing (it ships with Docker Desktop; update Docker Desktop)." }

Say "uv + Python 3.12"
if (-not (Has "uv")) {
  powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
  Refresh-Path
  if (-not (Has "uv")) { Fail "uv did not install. Install it manually: https://docs.astral.sh/uv/getting-started/installation/" }
}
Run "uv" @("python", "install", "3.12")
uv sync
if ($LASTEXITCODE -ne 0) {
  Write-Host "   Full install failed (usually PaddleOCR on Windows); installing without it (the app does not need it)." -ForegroundColor Yellow
  Run "uv" @("sync", "--no-group", "ocr")
  Run "uv" @("pip", "install", "pytesseract", "pypdfium2", "opencv-python-headless", "rapidfuzz", "indic-transliteration", "pillow")
}

Say "Node.js + web packages"
$needNode = $true
if (Has "node") { $major = [int]((node -v).TrimStart("v").Split(".")[0]); if ($major -ge 20) { $needNode = $false } }
if ($needNode) {
  if (-not (Has "winget")) { Fail "Node.js 20+ is required: install the LTS version from https://nodejs.org , then run this again." }
  winget install -e --id OpenJS.NodeJS.LTS --accept-source-agreements --accept-package-agreements
  Refresh-Path
  if (-not (Has "node")) { Fail "Node.js installed; close this window and run SETUP-WINDOWS.cmd again (Windows needs a new window to see it)." }
}
Push-Location web
Run "npm.cmd" @("ci", "--no-audit", "--no-fund")
if (-not (Test-Path ".env")) { Copy-Item ".env.example" ".env" }
Pop-Location

Say "Headless Chromium (PDF reports)"
uv run playwright install chromium
if ($LASTEXITCODE -ne 0) { Write-Host "   (PDF report download will not work until: uv run playwright install chromium)" -ForegroundColor Yellow }

Say "Configuration (.env)"
if (-not (Test-Path ".env")) {
  Copy-Item ".env.example" ".env"
  Write-Host "   Created .env. Add the free API keys (GEMINI/GROQ/COHERE) to use the Agent console."
}

Say "Docker images (database, OCR tools) - first time takes a few minutes"
Run "docker" @("compose", "build", "db")
docker compose --profile tools build ocr-tools
if ($LASTEXITCODE -ne 0) { Write-Host "   (OCR image failed to build: uploads will not work; everything else will)" -ForegroundColor Yellow }

Say "Database"
Run "docker" @("compose", "up", "-d", "db")
for ($i = 0; $i -lt 60; $i++) {
  docker compose exec -T db pg_isready -U nila -d nilasaatchi *> $null
  if ($LASTEXITCODE -eq 0) { break }
  Start-Sleep -Seconds 2
}
$n = "$(docker compose exec -T db psql -U nila -d nilasaatchi -Atc "select count(*) from information_schema.tables where table_name='document'")".Trim()
if ($n -eq "0" -and (Test-Path "bundle\nilasaatchi.dump")) {
  Write-Host "   Restoring the database dump (a few minutes)..."
  # roles are not part of a dump: create the agent's read-only role first (same as migration 0003)
  $roleSql = 'DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = ''agent_ro'') THEN CREATE ROLE agent_ro NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT; END IF; END $$; GRANT agent_ro TO nila;'
  Run "docker" @("compose", "exec", "-T", "db", "psql", "-U", "nila", "-d", "nilasaatchi", "-qc", $roleSql)
  # copy the dump into the container (piping binary data through PowerShell would corrupt it)
  Run "docker" @("compose", "cp", "bundle\nilasaatchi.dump", "db:/tmp/nilasaatchi.dump")
  New-Item -ItemType Directory -Force -Path "data\logs" | Out-Null
  docker compose exec -T db pg_restore -U nila -d nilasaatchi --no-owner /tmp/nilasaatchi.dump 2> "data\logs\restore.log"
  Write-Host "   (messages about existing extensions are expected; details in data\logs\restore.log)"
} elseif ($n -eq "0") {
  Write-Host "   No bundle\nilasaatchi.dump found: creating an empty schema (no data)"
} else {
  Write-Host "   Database already has data: kept"
}
Run "uv" @("run", "python", "scripts/migrate.py")
docker compose exec -T db psql -U nila -d nilasaatchi -Atc "select 'documents: ' || count(*) from document"

Say "Data files (page images, satellite tables, models)"
if ((Test-Path "bundle\data.tar.gz") -and -not (Test-Path "data\pages")) {
  Run "tar" @("-xzf", "bundle\data.tar.gz", "-C", $Root)
  Write-Host "   data\ restored"
} else { Write-Host "   data\ already present (or no bundle\data.tar.gz)" }

Say "Setup complete. Start the app with START-WINDOWS.cmd (double-click)."
