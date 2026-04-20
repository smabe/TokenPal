# TokenPal launcher (Windows) - auto-syncs deps when pyproject.toml changes.
#
# Runs 'pip install -e .[<extras>]' when the venv marker is older than
# pyproject.toml, then launches tokenpal with the passed args. Fast path
# (no pip call) when nothing has changed since last launch.
#
# Usage:
#   .\scripts\run-tokenpal.ps1 [tokenpal-args...]
#
# Force a full resync:
#   $env:TOKENPAL_FORCE_SYNC = "1"; .\scripts\run-tokenpal.ps1

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoDir = Split-Path -Parent $ScriptDir
$VenvDir = Join-Path $RepoDir ".venv"
$PyProject = Join-Path $RepoDir "pyproject.toml"
$Marker = Join-Path $VenvDir ".tokenpal-deps-synced"
$Python = Join-Path $VenvDir "Scripts\python.exe"
$Pip = Join-Path $VenvDir "Scripts\pip.exe"

if (-not (Test-Path $VenvDir)) {
    Write-Host "No venv at $VenvDir." -ForegroundColor Red
    Write-Host "Run: .\scripts\install-windows.ps1" -ForegroundColor Yellow
    exit 1
}

$needsSync = $false
if ($env:TOKENPAL_FORCE_SYNC -eq "1") {
    $needsSync = $true
} elseif (-not (Test-Path $Marker)) {
    $needsSync = $true
} else {
    $markerTime = (Get-Item $Marker).LastWriteTime
    $pyTime = (Get-Item $PyProject).LastWriteTime
    if ($pyTime -gt $markerTime) {
        $needsSync = $true
    }
}

if ($needsSync) {
    # Default to windows,dev extras. Server boxes add ',server' via env var
    # if they want the server-only deps during sync.
    $extras = if ($env:TOKENPAL_EXTRAS) { $env:TOKENPAL_EXTRAS } else { "windows,dev" }
    Write-Host "Syncing tokenpal[$extras]..." -ForegroundColor Yellow
    & $Pip install -e "$RepoDir[$extras]" --quiet
    if ($LASTEXITCODE -eq 0) {
        New-Item -ItemType File -Path $Marker -Force | Out-Null
        Write-Host "Dependencies synced." -ForegroundColor Green
    } else {
        Write-Host "pip install failed. Launching anyway - tokenpal may crash on missing imports." -ForegroundColor Red
    }
}

$Tokenpal = Join-Path $VenvDir "Scripts\tokenpal.exe"
if (Test-Path $Tokenpal) {
    & $Tokenpal $args
} else {
    & $Python -m tokenpal $args
}
exit $LASTEXITCODE
