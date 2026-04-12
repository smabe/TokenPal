# TokenPal Server Installer — Windows
# Sets up: Python venv, tokenpal[server], Ollama, Windows Firewall rule, startup shortcut

$ErrorActionPreference = "Stop"
$InstallDir = if ($env:TOKENPAL_SERVER_DIR) { $env:TOKENPAL_SERVER_DIR } else { "$env:USERPROFILE\.tokenpal" }
$VenvDir = "$InstallDir\server-venv"
$Port = if ($env:TOKENPAL_PORT) { $env:TOKENPAL_PORT } else { "8585" }

Write-Host "=== TokenPal Server Setup ===" -ForegroundColor Cyan
Write-Host "Install dir: $InstallDir"
Write-Host ""

# --- Phase 1: Python check ---
Write-Host "[1/6] Checking Python..." -ForegroundColor Yellow
try {
    $pyVer = & py -3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>&1
    $pyMinor = & py -3 -c "import sys; print(sys.version_info.minor)" 2>&1
    if ([int]$pyMinor -lt 12) {
        Write-Host "ERROR: Python 3.12+ required, found $pyVer" -ForegroundColor Red
        exit 1
    }
    Write-Host "  Python $pyVer OK"
} catch {
    Write-Host "ERROR: Python not found. Install from https://python.org" -ForegroundColor Red
    exit 1
}

# --- Phase 2: Ollama check ---
Write-Host "[2/6] Checking Ollama..." -ForegroundColor Yellow
$ollamaPath = Get-Command ollama -ErrorAction SilentlyContinue
if (-not $ollamaPath) {
    Write-Host "  Ollama not found. Install from https://ollama.com/download" -ForegroundColor Red
    Write-Host "  Or: winget install Ollama.Ollama"
    exit 1
}
Write-Host "  Ollama OK"

# --- Phase 3: Venv + tokenpal[server] ---
Write-Host "[3/6] Setting up Python environment..." -ForegroundColor Yellow
if (-not (Test-Path $InstallDir)) { New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null }
& py -3 -m venv $VenvDir
& "$VenvDir\Scripts\pip.exe" install --upgrade pip -q
& "$VenvDir\Scripts\pip.exe" install "tokenpal[server]" -q
Write-Host "  tokenpal-server installed"

# --- Phase 4: Firewall ---
Write-Host "[4/6] Configuring Windows Firewall..." -ForegroundColor Yellow
$ruleName = "TokenPal Server (TCP $Port)"
$existing = Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue
if (-not $existing) {
    try {
        New-NetFirewallRule -DisplayName $ruleName `
            -Direction Inbound -Protocol TCP -LocalPort $Port `
            -Action Allow -Profile Private | Out-Null
        Write-Host "  Firewall rule added (Private profile only)"
    } catch {
        Write-Host "  WARNING: Could not add firewall rule. Run as admin or add manually." -ForegroundColor Yellow
        Write-Host "  netsh advfirewall firewall add rule name='$ruleName' dir=in action=allow protocol=TCP localport=$Port"
    }
} else {
    Write-Host "  Firewall rule already exists"
}

# --- Phase 5: HF Token ---
Write-Host "[5/6] HuggingFace token (for gated models like Gemma)..." -ForegroundColor Yellow
if (-not $env:HF_TOKEN) {
    $hfToken = Read-Host "  Paste your HF token (or press Enter to skip)"
    if ($hfToken) {
        [System.Environment]::SetEnvironmentVariable("HF_TOKEN", $hfToken, "User")
        Write-Host "  HF_TOKEN saved (persistent via setx)"
    } else {
        Write-Host "  Skipped. Set HF_TOKEN later for gated models."
    }
} else {
    Write-Host "  HF_TOKEN already set."
}

# --- Phase 6: Startup shortcut ---
Write-Host "[6/6] Creating startup helper..." -ForegroundColor Yellow
$batPath = "$InstallDir\run-server.bat"
@"
@echo off
cd /d "$InstallDir"
call "$VenvDir\Scripts\activate.bat"
tokenpal-server --host 0.0.0.0 --port $Port
pause
"@ | Set-Content -Path $batPath -Encoding ASCII
Write-Host "  Created $batPath"

# Offer to add to startup folder
$startupDir = [System.IO.Path]::Combine($env:APPDATA, "Microsoft\Windows\Start Menu\Programs\Startup")
$answer = Read-Host "  Add to Windows startup? (y/N)"
if ($answer -eq "y") {
    $shortcut = "$startupDir\TokenPal Server.lnk"
    $ws = New-Object -ComObject WScript.Shell
    $sc = $ws.CreateShortcut($shortcut)
    $sc.TargetPath = "cmd.exe"
    $sc.Arguments = "/c `"$batPath`""
    $sc.WorkingDirectory = $InstallDir
    $sc.Save()
    Write-Host "  Startup shortcut created. Server will auto-start on login."
}

Write-Host ""
Write-Host "=== Setup Complete ===" -ForegroundColor Green
$hostname = [System.Net.Dns]::GetHostName()
Write-Host "Start the server:"
Write-Host "  $batPath"
Write-Host ""
Write-Host "Test from another machine:"
Write-Host "  curl http://${hostname}:${Port}/api/v1/server/info"
Write-Host ""
Write-Host "Client config.toml:"
Write-Host "  [llm]"
Write-Host "  api_url = `"http://${hostname}:${Port}/v1`""
