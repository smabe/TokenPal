# TokenPal Server Installer — Windows
# Sets up: Python, Ollama, model, venv, tokenpal[server], firewall, startup
# Run from inside the cloned TokenPal repo.

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoDir = Split-Path -Parent $ScriptDir
$InstallDir = if ($env:TOKENPAL_SERVER_DIR) { $env:TOKENPAL_SERVER_DIR } else { "$env:USERPROFILE\.tokenpal" }
$Port = if ($env:TOKENPAL_PORT) { $env:TOKENPAL_PORT } else { "8585" }
$Model = if ($env:TOKENPAL_MODEL) { $env:TOKENPAL_MODEL } else { "gemma4" }

Write-Host "=== TokenPal Server Setup ===" -ForegroundColor Cyan
Write-Host "Repo: $RepoDir"
Write-Host ""

# --- Phase 1: Python ---
Write-Host "[1/7] Checking Python..." -ForegroundColor Yellow
$pyOk = $false
try {
    $pyMinor = & py -3 -c "import sys; print(sys.version_info.minor)" 2>&1
    if ([int]$pyMinor -ge 12) { $pyOk = $true }
} catch {}
if (-not $pyOk) {
    Write-Host "  Python 3.12+ not found. Installing via winget..."
    winget install Python.Python.3.12 --accept-source-agreements --accept-package-agreements
    # Verify after install
    try {
        $pyMinor = & py -3 -c "import sys; print(sys.version_info.minor)" 2>&1
        if ([int]$pyMinor -ge 12) { $pyOk = $true }
    } catch {}
    if (-not $pyOk) {
        Write-Host "ERROR: Python installed but py launcher not found. Restart terminal and re-run." -ForegroundColor Red
        exit 1
    }
}
$pyVer = & py -3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>&1
Write-Host "  Python $pyVer OK"

# --- Phase 2: Ollama ---
Write-Host "[2/7] Checking Ollama..." -ForegroundColor Yellow
$ollamaExe = "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe"
$ollamaInPath = Get-Command ollama -ErrorAction SilentlyContinue
if (-not $ollamaInPath -and -not (Test-Path $ollamaExe)) {
    Write-Host "  Ollama not found. Installing via winget..."
    winget install Ollama.Ollama --accept-source-agreements --accept-package-agreements
    if (-not (Test-Path $ollamaExe)) {
        Write-Host "ERROR: Ollama installed but not found. Restart terminal and re-run." -ForegroundColor Red
        exit 1
    }
}
if ($ollamaInPath) {
    $ollamaExe = $ollamaInPath.Source
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
$VenvDir = "$RepoDir\.venv"
if (-not (Test-Path $VenvDir)) {
    & py -3 -m venv $VenvDir
}
& "$VenvDir\Scripts\pip.exe" install --upgrade pip -q
& "$VenvDir\Scripts\pip.exe" install -e "$RepoDir[server]" -q
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
Write-Host "[6/7] HuggingFace token (for fine-tuning gated models like Gemma)..." -ForegroundColor Yellow
if (-not $env:HF_TOKEN) {
    $hfToken = Read-Host "  Paste your HF token (or press Enter to skip)"
    if ($hfToken) {
        [System.Environment]::SetEnvironmentVariable("HF_TOKEN", $hfToken, "User")
        Write-Host "  HF_TOKEN saved (persistent)"
    } else {
        Write-Host "  Skipped. Only needed for fine-tuning gated models."
    }
} else {
    Write-Host "  HF_TOKEN already set."
}

# --- Phase 7: Startup shortcut ---
Write-Host "[7/7] Creating startup helper..." -ForegroundColor Yellow
$batPath = "$RepoDir\start-server.bat"
@"
@echo off
cd /d $RepoDir
set OLLAMA_VULKAN=1
set OLLAMA_KEEP_ALIVE=1m
start "" /B "$ollamaExe" serve
timeout /t 3 /nobreak >nul
call .venv\Scripts\activate.bat
tokenpal-server --host 0.0.0.0 --port $Port
pause
"@ | Set-Content -Path $batPath -Encoding ASCII
Write-Host "  Created $batPath"

$startupDir = [System.IO.Path]::Combine($env:APPDATA, "Microsoft\Windows\Start Menu\Programs\Startup")
$answer = Read-Host "  Add to Windows startup? (y/N)"
if ($answer -eq "y") {
    $shortcut = "$startupDir\TokenPal Server.lnk"
    $ws = New-Object -ComObject WScript.Shell
    $sc = $ws.CreateShortcut($shortcut)
    $sc.TargetPath = "cmd.exe"
    $sc.Arguments = "/c `"$batPath`""
    $sc.WorkingDirectory = $RepoDir
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
