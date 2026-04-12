# TokenPal Server Installer — Windows
# Sets up: Ollama, model, Python venv, tokenpal[server], firewall, startup shortcut

$ErrorActionPreference = "Stop"
$InstallDir = if ($env:TOKENPAL_SERVER_DIR) { $env:TOKENPAL_SERVER_DIR } else { "$env:USERPROFILE\.tokenpal" }
$VenvDir = "$InstallDir\server-venv"
$Port = if ($env:TOKENPAL_PORT) { $env:TOKENPAL_PORT } else { "8585" }
$Model = if ($env:TOKENPAL_MODEL) { $env:TOKENPAL_MODEL } else { "gemma4" }

Write-Host "=== TokenPal Server Setup ===" -ForegroundColor Cyan
Write-Host "Install dir: $InstallDir"
Write-Host ""

# --- Phase 1: Python check ---
Write-Host "[1/7] Checking Python..." -ForegroundColor Yellow
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

# --- Phase 2: Ollama install ---
Write-Host "[2/7] Checking Ollama..." -ForegroundColor Yellow
$ollamaExe = "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe"
$ollamaInPath = Get-Command ollama -ErrorAction SilentlyContinue
if (-not $ollamaInPath -and -not (Test-Path $ollamaExe)) {
    Write-Host "  Ollama not found. Installing via winget..."
    winget install Ollama.Ollama --accept-source-agreements --accept-package-agreements
    # winget install doesn't update PATH in current session
    if (-not (Test-Path $ollamaExe)) {
        Write-Host "ERROR: Ollama installed but not found at expected path." -ForegroundColor Red
        Write-Host "  Restart your terminal and re-run this script."
        exit 1
    }
}
# Resolve the actual ollama path for the rest of the script
if ($ollamaInPath) {
    $ollamaExe = $ollamaInPath.Source
} elseif (-not (Test-Path $ollamaExe)) {
    Write-Host "ERROR: Cannot find ollama.exe" -ForegroundColor Red
    exit 1
}
Write-Host "  Ollama OK: $ollamaExe"

# --- Phase 3: Start Ollama + pull model ---
Write-Host "[3/7] Ensuring Ollama is running and pulling model..." -ForegroundColor Yellow
try {
    $null = Invoke-WebRequest -Uri "http://localhost:11434/" -TimeoutSec 3 -ErrorAction Stop
} catch {
    Write-Host "  Starting Ollama..."
    Start-Process -FilePath $ollamaExe -ArgumentList "serve" -WindowStyle Hidden
    Start-Sleep -Seconds 5
}
$models = & $ollamaExe list 2>&1
if ($models -notmatch $Model) {
    Write-Host "  Pulling $Model (this may take a few minutes)..."
    & $ollamaExe pull $Model
} else {
    Write-Host "  Model $Model already available"
}

# --- Phase 4: Venv + tokenpal[server] ---
Write-Host "[4/7] Setting up Python environment..." -ForegroundColor Yellow
if (-not (Test-Path $InstallDir)) { New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null }
& py -3 -m venv $VenvDir
& "$VenvDir\Scripts\pip.exe" install --upgrade pip -q
& "$VenvDir\Scripts\pip.exe" install "tokenpal[server]" -q
Write-Host "  tokenpal-server installed"

# --- Phase 5: Firewall ---
Write-Host "[5/7] Configuring Windows Firewall..." -ForegroundColor Yellow
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

# --- Phase 6: HF Token ---
Write-Host "[6/7] HuggingFace token (for gated models like Gemma)..." -ForegroundColor Yellow
if (-not $env:HF_TOKEN) {
    $hfToken = Read-Host "  Paste your HF token (or press Enter to skip)"
    if ($hfToken) {
        [System.Environment]::SetEnvironmentVariable("HF_TOKEN", $hfToken, "User")
        Write-Host "  HF_TOKEN saved (persistent)"
    } else {
        Write-Host "  Skipped. Set HF_TOKEN later for gated models."
    }
} else {
    Write-Host "  HF_TOKEN already set."
}

# --- Phase 7: Startup shortcut ---
Write-Host "[7/7] Creating startup helper..." -ForegroundColor Yellow
$batPath = "$InstallDir\run-server.bat"
@"
@echo off
cd /d "$InstallDir"
start /B "$ollamaExe" serve
timeout /t 3 /nobreak >nul
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
