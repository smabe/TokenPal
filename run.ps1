# Quick launcher for TokenPal — activates venv and runs.
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Venv = Join-Path $ScriptDir ".venv"

if (-not (Test-Path $Venv)) {
    Write-Host "No .venv found. Run: python setup_tokenpal.py"
    exit 1
}

& (Join-Path $Venv "Scripts\Activate.ps1")
& tokenpal @args
