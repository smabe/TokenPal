# TokenPal — Unified Windows Installer
# Handles everything from a bare machine to a working TokenPal installation.
#
# Usage (standalone, paste into PowerShell):
#   iwr https://raw.githubusercontent.com/smabe/TokenPal/main/scripts/install-windows.ps1 -OutFile install.ps1; .\install.ps1
#
# Usage (with parameters):
#   .\install-windows.ps1 -Mode Client    # skip interactive prompt
#   .\install-windows.ps1 -Mode Server
#   .\install-windows.ps1 -Mode Both
#
# $ErrorActionPreference = "Stop" makes PowerShell treat all errors as terminating.
# This means any failed command (bad exit code, missing executable, etc.) will
# immediately halt the script instead of silently continuing with broken state.

param(
    [ValidateSet("Client", "Server", "Both")]
    [string]$Mode
)

$ErrorActionPreference = "Stop"

$RepoUrl   = "https://github.com/smabe/TokenPal.git"
$RepoDir   = if ($env:TOKENPAL_DIR) { $env:TOKENPAL_DIR } else { "$env:USERPROFILE\tokenpal" }
$Port      = if ($env:TOKENPAL_PORT) { $env:TOKENPAL_PORT } else { "8585" }
$Model     = if ($env:TOKENPAL_MODEL) { $env:TOKENPAL_MODEL } else { "gemma4" }

# ── Header ──────────────────────────────────────────────────────────────────

Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  TokenPal — Windows Installer" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

# ── Phase 0: Feature Selection ──────────────────────────────────────────────

Write-Host "[0/9] Installation mode" -ForegroundColor Yellow

if (-not $Mode) {
    if ([System.Environment]::UserInteractive -and $Host.UI.RawUI) {
        Write-Host ""
        Write-Host "  How would you like to install TokenPal?"
        Write-Host "    [B] Both         — full installation (recommended)"
        Write-Host "    [C] Client only  — run the buddy on this PC"
        Write-Host "    [S] Server only  — serve LLM inference for other machines"
        Write-Host ""
        do {
            $choice = Read-Host "  Choose [B/C/S] (default: B)"
            $choice = $choice.Trim().ToUpper()
            if (-not $choice) { $choice = "B" }
        } while ($choice -notin @("C", "S", "B"))
        switch ($choice) {
            "C" { $Mode = "Client" }
            "S" { $Mode = "Server" }
            "B" { $Mode = "Both" }
        }
    } else {
        Write-Host "  Non-interactive session detected, defaulting to Both"
        Write-Host "  (Override with -Mode Client or -Mode Server)"
        $Mode = "Both"
    }
}

$InstallClient = $Mode -in @("Client", "Both")
$InstallServer = $Mode -in @("Server", "Both")

Write-Host ""
Write-Host "  >>> Installing in $Mode mode <<<" -ForegroundColor Green
if ($InstallServer) {
    Write-Host "      - tokenpal-server (fastapi + uvicorn)" -ForegroundColor Gray
}
if ($InstallClient) {
    Write-Host "      - tokenpal (buddy + UI)" -ForegroundColor Gray
}

# ── Phase 1: Python 3.12+ ──────────────────────────────────────────────────

Write-Host ""
Write-Host "[1/9] Checking Python 3.12+..." -ForegroundColor Yellow

$pyOk = $false
try {
    $pyMinor = & py -3 -c "import sys; print(sys.version_info.minor)" 2>&1
    if ([int]$pyMinor -ge 12) { $pyOk = $true }
} catch {}

if (-not $pyOk) {
    Write-Host "  Python 3.12+ not found. Installing via winget..."
    winget install Python.Python.3.12 --accept-source-agreements --accept-package-agreements
    # Refresh PATH for this session
    $env:PATH = [System.Environment]::GetEnvironmentVariable("PATH", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("PATH", "User")
    try {
        $pyMinor = & py -3 -c "import sys; print(sys.version_info.minor)" 2>&1
        if ([int]$pyMinor -ge 12) { $pyOk = $true }
    } catch {}
    if (-not $pyOk) {
        Write-Host "  WARNING: Python installed but py launcher not found in this session." -ForegroundColor Red
        Write-Host "  Close this terminal, open a new one, and re-run this script." -ForegroundColor Red
        exit 1
    }
}

$pyVer = & py -3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')" 2>&1
Write-Host "  Python $pyVer OK" -ForegroundColor Green

# ── Phase 2: Git ────────────────────────────────────────────────────────────

Write-Host ""
Write-Host "[2/9] Checking Git..." -ForegroundColor Yellow

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Host "  Git not found. Installing via winget..."
    winget install Git.Git --accept-source-agreements --accept-package-agreements
    # Add to PATH for this session
    $gitPaths = @("$env:ProgramFiles\Git\cmd", "${env:ProgramFiles(x86)}\Git\cmd")
    foreach ($gp in $gitPaths) {
        if (Test-Path $gp) {
            $env:PATH += ";$gp"
            break
        }
    }
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        Write-Host "  WARNING: Git installed but not found in PATH." -ForegroundColor Red
        Write-Host "  Close this terminal, open a new one, and re-run this script." -ForegroundColor Red
        exit 1
    }
}

$gitVer = & git --version 2>&1
Write-Host "  $gitVer OK" -ForegroundColor Green

# ── Phase 3: Clone or Update Repo ──────────────────────────────────────────

Write-Host ""
Write-Host "[3/9] Setting up TokenPal repository..." -ForegroundColor Yellow

# Check if we're already inside a TokenPal repo (e.g., user ran script from clone)
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ScriptRepoDir = if ($ScriptDir) { Split-Path -Parent $ScriptDir } else { $null }
$InRepo = $false

if ($ScriptRepoDir -and (Test-Path "$ScriptRepoDir\pyproject.toml")) {
    $content = Get-Content "$ScriptRepoDir\pyproject.toml" -Raw -ErrorAction SilentlyContinue
    if ($content -match 'name\s*=\s*"tokenpal"') {
        $RepoDir = $ScriptRepoDir
        $InRepo = $true
        Write-Host "  Already inside TokenPal repo: $RepoDir"
    }
}

if (-not $InRepo) {
    if (Test-Path "$RepoDir\.git") {
        Write-Host "  Updating existing repo at $RepoDir..."
        Push-Location $RepoDir
        try { & git pull --ff-only } catch {
            Write-Host "  WARNING: git pull failed (local changes?). Continuing with existing code." -ForegroundColor Yellow
        }
        Pop-Location
    } else {
        Write-Host "  Cloning TokenPal to $RepoDir..."
        & git clone $RepoUrl $RepoDir
    }
}

Write-Host "  Repo: $RepoDir" -ForegroundColor Green

# ── Phase 4: Virtual Environment + Dependencies ────────────────────────────

Write-Host ""
Write-Host "[4/9] Setting up Python environment..." -ForegroundColor Yellow

$VenvDir = "$RepoDir\.venv"
if (-not (Test-Path $VenvDir)) {
    Write-Host "  Creating virtual environment..."
    & py -3 -m venv $VenvDir
} else {
    Write-Host "  Virtual environment already exists"
}

Write-Host "  Upgrading pip..."
& "$VenvDir\Scripts\python.exe" -m pip install --upgrade pip -q

# Determine extras based on mode
if ($InstallClient -and $InstallServer) {
    $extras = "windows,server,dev"
} elseif ($InstallServer) {
    $extras = "windows,server,dev"
} else {
    $extras = "windows,dev"
}

Write-Host "  Installing tokenpal[$extras]..."
& "$VenvDir\Scripts\pip.exe" install -e "$RepoDir[$extras]" -q
Write-Host "  Dependencies installed" -ForegroundColor Green

# ── Phase 5: AMD GPU Detection ─────────────────────────────────────────────

Write-Host ""
Write-Host "[5/9] Checking for AMD GPU..." -ForegroundColor Yellow

$amdGpu = $false
try {
    $gpus = Get-CimInstance -ClassName Win32_VideoController -ErrorAction SilentlyContinue
    foreach ($gpu in $gpus) {
        if ($gpu.Name -match "AMD|Radeon") {
            $amdGpu = $true
            Write-Host "  AMD GPU detected: $($gpu.Name)" -ForegroundColor Green
            break
        }
    }
} catch {}

if ($amdGpu) {
    Write-Host "  Setting Vulkan environment variables (recommended for AMD)..."
    [System.Environment]::SetEnvironmentVariable("OLLAMA_VULKAN", "1", "User")
    [System.Environment]::SetEnvironmentVariable("GGML_VK_VISIBLE_DEVICES", "0", "User")
    $env:OLLAMA_VULKAN = "1"
    $env:GGML_VK_VISIBLE_DEVICES = "0"
    Write-Host "  OLLAMA_VULKAN=1 and GGML_VK_VISIBLE_DEVICES=0 set as persistent User env vars"
    Write-Host "  NOTE: Vulkan is the recommended inference path for AMD GPUs." -ForegroundColor Cyan
} else {
    Write-Host "  No AMD GPU detected (NVIDIA/Intel will be auto-detected by Ollama)"
}

# ── Phase 6: Ollama + Model ────────────────────────────────────────────────

Write-Host ""
Write-Host "[6/9] Setting up Ollama..." -ForegroundColor Yellow

$ollamaExe = $null
$ollamaInPath = Get-Command ollama -ErrorAction SilentlyContinue
$ollamaLocalApp = "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe"

if ($ollamaInPath) {
    $ollamaExe = $ollamaInPath.Source
} elseif (Test-Path $ollamaLocalApp) {
    $ollamaExe = $ollamaLocalApp
}

if (-not $ollamaExe) {
    Write-Host "  Ollama not found. Installing via winget..."
    winget install Ollama.Ollama --accept-source-agreements --accept-package-agreements
    # Refresh PATH
    $env:PATH = [System.Environment]::GetEnvironmentVariable("PATH", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("PATH", "User")
    $ollamaInPath = Get-Command ollama -ErrorAction SilentlyContinue
    if ($ollamaInPath) {
        $ollamaExe = $ollamaInPath.Source
    } elseif (Test-Path $ollamaLocalApp) {
        $ollamaExe = $ollamaLocalApp
    }
    if (-not $ollamaExe) {
        Write-Host "  WARNING: Ollama installed but not found. Restart terminal and re-run." -ForegroundColor Red
        exit 1
    }
}

Write-Host "  Ollama: $ollamaExe"

# Health check — start if not running, poll up to 30s
$ollamaRunning = $false
try {
    $null = Invoke-WebRequest -Uri "http://localhost:11434/" -TimeoutSec 3 -ErrorAction Stop
    $ollamaRunning = $true
} catch {}

if (-not $ollamaRunning) {
    Write-Host "  Starting Ollama (AMD+Vulkan cold start can take ~45s)..."
    Start-Process -FilePath $ollamaExe -ArgumentList "serve" -WindowStyle Hidden

    $waited = 0
    $maxWait = 60
    while ($waited -lt $maxWait) {
        Start-Sleep -Seconds 2
        $waited += 2
        try {
            $null = Invoke-WebRequest -Uri "http://localhost:11434/" -TimeoutSec 2 -ErrorAction Stop
            $ollamaRunning = $true
            break
        } catch {}
        if ($waited % 10 -eq 0) {
            Write-Host "  Waiting for Ollama... ($waited/$maxWait s)"
        }
    }

    # Last-ditch check — some Ollama builds keep initializing for a while
    # after they bind the port. Give it one more long-timeout probe.
    if (-not $ollamaRunning) {
        Write-Host "  Giving Ollama one more try with 10s timeout..."
        try {
            $null = Invoke-WebRequest -Uri "http://localhost:11434/" -TimeoutSec 10 -ErrorAction Stop
            $ollamaRunning = $true
        } catch {}
    }

    if (-not $ollamaRunning) {
        Write-Host "  WARNING: Ollama did not respond after ${maxWait}s." -ForegroundColor Red
        Write-Host "  It may still be starting. Try: curl http://localhost:11434/" -ForegroundColor Yellow
        Write-Host "  If still unreachable, start manually: $ollamaExe serve" -ForegroundColor Yellow
        Write-Host "  Then pull the model: ollama pull $Model" -ForegroundColor Yellow
    }
}

if ($ollamaRunning) {
    Write-Host "  Ollama is running" -ForegroundColor Green

    # Recommend model based on VRAM (server/both mode)
    if ($InstallServer) {
        $vramGB = 0
        # Try NVIDIA first
        try {
            $nvOut = & nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>&1
            if ($LASTEXITCODE -eq 0 -and $nvOut -match '^\d+') {
                $vramGB = [math]::Floor([int]$nvOut / 1024)
            }
        } catch {}
        # Try AMD via WMI (often capped at 4GB — fall back to system RAM)
        if ($vramGB -eq 0 -and $amdGpu) {
            $totalRAM = [math]::Floor((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB)
            $vramGB = $totalRAM  # Vulkan can use system RAM, total is the budget
            Write-Host "  AMD GPU: using system RAM (${totalRAM}GB) as memory budget for model recommendation" -ForegroundColor Cyan
        }
        # NVIDIA fallback to system RAM
        if ($vramGB -eq 0) {
            $vramGB = [math]::Floor((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB)
        }

        if ($vramGB -ge 16) {
            $Recommended = "gemma4:26b"
            Write-Host "  Detected ~${vramGB}GB — recommending gemma4:26b (26B, best quality)" -ForegroundColor Cyan
        } elseif ($vramGB -ge 8) {
            $Recommended = "gemma4"
            Write-Host "  Detected ~${vramGB}GB — recommending gemma4 (9B, solid default)" -ForegroundColor Cyan
        } else {
            $Recommended = "gemma4"
            Write-Host "  Detected ~${vramGB}GB — recommending gemma4 (9B)" -ForegroundColor Cyan
        }

        # Let user confirm or override
        if ([System.Environment]::UserInteractive -and $Host.UI.RawUI -and $Model -eq "gemma4") {
            $modelChoice = Read-Host "  Pull $Recommended? [Y/n/other model name]"
            $modelChoice = $modelChoice.Trim()
            if (-not $modelChoice -or $modelChoice -eq "y" -or $modelChoice -eq "Y") {
                $Model = $Recommended
            } elseif ($modelChoice -eq "n" -or $modelChoice -eq "N") {
                $Model = ""
            } else {
                $Model = $modelChoice
            }
        } else {
            $Model = $Recommended
        }
    }

    # Pull model if missing
    if (-not $Model) {
        Write-Host "  Skipping model pull" -ForegroundColor DarkGray
    } else {
        $models = & $ollamaExe list 2>&1
        if ($models -notmatch [regex]::Escape($Model)) {
            Write-Host "  Pulling $Model (this may take a few minutes)..."
            & $ollamaExe pull $Model
            Write-Host "  Model $Model pulled" -ForegroundColor Green
        } else {
            Write-Host "  Model $Model already available" -ForegroundColor Green
        }
    }
}

# ── Phase 7: Server Setup (server/both mode only) ──────────────────────────

if ($InstallServer) {
    Write-Host ""
    Write-Host "[7/9] Configuring server..." -ForegroundColor Yellow

    # Firewall rule
    $ruleName = "TokenPal Server (TCP $Port)"
    $existing = Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue
    if (-not $existing) {
        try {
            New-NetFirewallRule -DisplayName $ruleName `
                -Direction Inbound -Protocol TCP -LocalPort $Port `
                -Action Allow -Profile Private | Out-Null
            Write-Host "  Firewall rule added (Private profile, TCP $Port)" -ForegroundColor Green
        } catch {
            Write-Host "  WARNING: Could not add firewall rule (need admin)." -ForegroundColor Yellow
            Write-Host "  Run manually:" -ForegroundColor Yellow
            Write-Host "  netsh advfirewall firewall add rule name='$ruleName' dir=in action=allow protocol=TCP localport=$Port"
        }
    } else {
        Write-Host "  Firewall rule already exists"
    }

    # start-server.bat
    $vulkanLine = if ($amdGpu) { "set OLLAMA_VULKAN=1" } else { "rem set OLLAMA_VULKAN=1  (uncomment for AMD GPU)" }
    $batPath = "$RepoDir\start-server.bat"
    @"
@echo off
cd /d $RepoDir
$vulkanLine
set OLLAMA_KEEP_ALIVE=24h
start "" /B "$ollamaExe" serve
timeout /t 5 /nobreak >nul
call .venv\Scripts\activate.bat
tokenpal-server --host 0.0.0.0 --port $Port
pause
"@ | Set-Content -Path $batPath -Encoding ASCII
    Write-Host "  Created $batPath" -ForegroundColor Green

    # Optional Windows Startup shortcut
    if ([System.Environment]::UserInteractive -and $Host.UI.RawUI) {
        $answer = Read-Host "  Add server to Windows startup? (y/N)"
        if ($answer -eq "y") {
            $startupDir = [System.IO.Path]::Combine($env:APPDATA, "Microsoft\Windows\Start Menu\Programs\Startup")
            $shortcut = "$startupDir\TokenPal Server.lnk"
            $ws = New-Object -ComObject WScript.Shell
            $sc = $ws.CreateShortcut($shortcut)
            $sc.TargetPath = "cmd.exe"
            $sc.Arguments = "/c `"$batPath`""
            $sc.WorkingDirectory = $RepoDir
            $sc.Save()
            Write-Host "  Startup shortcut created" -ForegroundColor Green
        }
    }

    # HuggingFace token
    Write-Host ""
    Write-Host "  HuggingFace token (for fine-tuning gated models like Gemma)..." -ForegroundColor Yellow
    if (-not $env:HF_TOKEN) {
        if ([System.Environment]::UserInteractive -and $Host.UI.RawUI) {
            $hfToken = Read-Host "  Paste your HF token (or press Enter to skip)"
            if ($hfToken) {
                [System.Environment]::SetEnvironmentVariable("HF_TOKEN", $hfToken, "User")
                Write-Host "  HF_TOKEN saved as persistent User env var" -ForegroundColor Green
            } else {
                Write-Host "  Skipped. Only needed for fine-tuning gated models."
            }
        } else {
            Write-Host "  Non-interactive — set HF_TOKEN manually if needed for fine-tuning."
        }
    } else {
        Write-Host "  HF_TOKEN already set"
    }
} else {
    Write-Host ""
    Write-Host "[7/9] Server setup — skipped (client-only mode)" -ForegroundColor DarkGray
}

# ── Phase 8: Config File ───────────────────────────────────────────────────

Write-Host ""
Write-Host "[8/9] Setting up config..." -ForegroundColor Yellow

$configPath = "$RepoDir\config.toml"
$defaultConfig = "$RepoDir\config.default.toml"

if (Test-Path $configPath) {
    Write-Host "  config.toml already exists" -ForegroundColor Green
} elseif (Test-Path $defaultConfig) {
    Copy-Item $defaultConfig $configPath
    Write-Host "  Created config.toml from defaults" -ForegroundColor Green
} else {
    Write-Host "  WARNING: config.default.toml not found — config setup skipped" -ForegroundColor Yellow
}

# ── Phase 9: Validation ────────────────────────────────────────────────────

Write-Host ""
Write-Host "[9/9] Running validation..." -ForegroundColor Yellow

$tokenpalExe = "$VenvDir\Scripts\tokenpal.exe"
if (Test-Path $tokenpalExe) {
    try {
        & $tokenpalExe --validate
        Write-Host "  Validation passed" -ForegroundColor Green
    } catch {
        Write-Host "  WARNING: Validation returned errors (see above). Non-fatal." -ForegroundColor Yellow
    }
} else {
    # Fallback: basic import check
    Write-Host "  tokenpal.exe not found, running import check..."
    try {
        & "$VenvDir\Scripts\python.exe" -c "from tokenpal.app import main; print('OK')"
        Write-Host "  Import check passed" -ForegroundColor Green
    } catch {
        Write-Host "  WARNING: Import check failed" -ForegroundColor Yellow
    }
}

# ── Summary ─────────────────────────────────────────────────────────────────

Write-Host ""
Write-Host "============================================" -ForegroundColor Green
Write-Host "  TokenPal installation complete!" -ForegroundColor Green
Write-Host "============================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Mode:    $Mode" -ForegroundColor White
Write-Host "  Repo:    $RepoDir" -ForegroundColor White
Write-Host "  Venv:    $VenvDir" -ForegroundColor White
Write-Host "  Model:   $Model" -ForegroundColor White
Write-Host ""

if ($InstallClient) {
    Write-Host "  --- Client ---" -ForegroundColor Cyan
    Write-Host "  1. Activate venv:  $VenvDir\Scripts\Activate.ps1"
    Write-Host "  2. Run TokenPal:   tokenpal"
    Write-Host "  3. Health check:   tokenpal --check"
    Write-Host ""
    Write-Host "  On first run, TokenPal walks you through a quick setup wizard." -ForegroundColor DarkGray
    Write-Host ""
}

if ($InstallServer) {
    $hostname = [System.Net.Dns]::GetHostName()
    Write-Host "  --- Server ---" -ForegroundColor Cyan
    Write-Host "  Start server:  $RepoDir\start-server.bat"
    Write-Host ""
    Write-Host "  Client config.toml:" -ForegroundColor White
    Write-Host "    [llm]"
    Write-Host "    api_url = `"http://${hostname}:${Port}/v1`""
    Write-Host ""
}

Write-Host "  Config:  $RepoDir\config.toml"
Write-Host "  Logs:    ~\.tokenpal\logs\tokenpal.log"
Write-Host ""
