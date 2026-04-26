# Quick launcher for TokenPal - activates venv, auto-syncs deps if
# pyproject.toml changed since last launch, then runs tokenpal.
#
# Force a full resync: $env:TOKENPAL_FORCE_SYNC = "1"; .\run.ps1
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Venv = Join-Path $ScriptDir ".venv"
$PyProject = Join-Path $ScriptDir "pyproject.toml"
$Marker = Join-Path $Venv ".tokenpal-deps-synced"
$Pip = Join-Path $Venv "Scripts\pip.exe"

if (-not (Test-Path $Venv)) {
    Write-Host "No .venv found. Run: python setup_tokenpal.py"
    exit 1
}

& (Join-Path $Venv "Scripts\Activate.ps1")

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
    $extras = if ($env:TOKENPAL_EXTRAS) { $env:TOKENPAL_EXTRAS } else { "windows,desktop,dev" }
    Write-Host "Syncing tokenpal[$extras]..." -ForegroundColor Yellow
    & $Pip install -e "$ScriptDir[$extras]" --quiet
    if ($LASTEXITCODE -eq 0) {
        New-Item -ItemType File -Path $Marker -Force | Out-Null
        Write-Host "Dependencies synced." -ForegroundColor Green
    } else {
        Write-Host "pip install failed. Launching anyway - tokenpal may crash on missing imports." -ForegroundColor Red
    }
}

& tokenpal @args
exit $LASTEXITCODE
