# TokenPal Server — One-Line Bootstrap
# Paste this into PowerShell on the GPU machine:
#   powershell -Command "iwr https://raw.githubusercontent.com/smabe/TokenPal/main/scripts/bootstrap.ps1 -OutFile bootstrap.ps1; .\bootstrap.ps1"

# DEPRECATED: This script is superseded by install-windows.ps1.
# It is kept for backward compatibility only.

Write-Host "NOTE: This script is maintained for backward compatibility." -ForegroundColor Yellow
Write-Host "For fresh installs, prefer: powershell scripts\install-windows.ps1" -ForegroundColor Yellow
Write-Host ""

$ErrorActionPreference = "Stop"
$RepoUrl = "https://github.com/smabe/TokenPal.git"
$InstallDir = "$env:USERPROFILE\tokenpal-server"

Write-Host ""
Write-Host "  TokenPal Server Bootstrap" -ForegroundColor Cyan
Write-Host "  =========================" -ForegroundColor Cyan
Write-Host ""

# Check for git
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Host "Installing git..." -ForegroundColor Yellow
    winget install Git.Git --accept-source-agreements --accept-package-agreements
    $env:PATH += ";$env:ProgramFiles\Git\cmd"
}

# Clone or update
if (Test-Path "$InstallDir\.git") {
    Write-Host "Updating existing repo..." -ForegroundColor Yellow
    Push-Location $InstallDir
    git pull
    Pop-Location
} else {
    Write-Host "Cloning TokenPal..." -ForegroundColor Yellow
    git clone $RepoUrl $InstallDir
}

# Run the full installer
Write-Host ""
Push-Location $InstallDir
powershell -ExecutionPolicy Bypass -File scripts\install-server.ps1
Pop-Location

# Print connection info
Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  Server is ready!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""

$hostname = [System.Net.Dns]::GetHostName()
$Port = if ($env:TOKENPAL_PORT) { $env:TOKENPAL_PORT } else { "8585" }

# Try to get Tailscale hostname
$tsHostname = $null
try {
    $tsStatus = & tailscale status --json 2>$null | ConvertFrom-Json
    if ($tsStatus.Self.DNSName) {
        $tsHostname = $tsStatus.Self.DNSName.TrimEnd('.')
    }
} catch {}

Write-Host "Tell your friends to add this to their config.toml:" -ForegroundColor Yellow
Write-Host ""
Write-Host "  [llm]"
if ($tsHostname) {
    Write-Host "  api_url = `"http://${tsHostname}:${Port}/v1`"    # Tailscale" -ForegroundColor White
    Write-Host ""
    Write-Host "  Or on local network:"
}
Write-Host "  api_url = `"http://${hostname}:${Port}/v1`"" -ForegroundColor White
Write-Host ""
Write-Host "Start the server anytime:"
Write-Host "  $InstallDir\start-server.bat" -ForegroundColor White
Write-Host ""
