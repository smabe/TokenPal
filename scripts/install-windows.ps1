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

# lemonade-sdk llamacpp-rocm release pin. Bump as a maintenance PR when we
# bless a newer nightly. Override with TOKENPAL_LEMONADE_TAG for testing.
$LemonadeTag = if ($env:TOKENPAL_LEMONADE_TAG) { $env:TOKENPAL_LEMONADE_TAG } else { "b1241" }
$LlamacppDir = "$env:LOCALAPPDATA\TokenPal\llamacpp-rocm"
$ModelsDir   = "$env:LOCALAPPDATA\TokenPal\models"

function Get-AmdGpuVramGB {
    # Returns VRAM in GB for the largest AMD GPU, or 0 if none / unreadable.
    # Reads HardwareInformation.qwMemorySize (UINT64) from the video class
    # registry key. Win32_VideoController.AdapterRAM is UINT32 and caps at
    # 4 GB, so it is useless on any modern dGPU.
    $gpuClass = "HKLM:\SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
    $maxBytes = 0L
    if (Test-Path $gpuClass) {
        Get-ChildItem $gpuClass -ErrorAction SilentlyContinue | ForEach-Object {
            $props = Get-ItemProperty $_.PSPath -ErrorAction SilentlyContinue
            if ($props -and $props.ProviderName -match "AMD|Advanced Micro|ATI") {
                $qwm = $props."HardwareInformation.qwMemorySize"
                if ($qwm -and [int64]$qwm -gt $maxBytes) { $maxBytes = [int64]$qwm }
            }
        }
    }
    if ($maxBytes -gt 0) { return [math]::Floor($maxBytes / 1GB) }
    return 0
}

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
$amdDGpu = $false
$useLlamacpp = $false
$amdVramGB = 0

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
    # iGPU vs dGPU: integrated graphics share system RAM and rarely show
    # qwMemorySize >= 6 GB in the video class registry. Anything above that
    # threshold is safe to treat as discrete for install-time decisions.
    $amdVramGB = Get-AmdGpuVramGB
    if ($amdVramGB -ge 6) {
        $amdDGpu = $true
        Write-Host "  Discrete AMD GPU with ~${amdVramGB} GB VRAM" -ForegroundColor Cyan
    } else {
        Write-Host "  AMD iGPU (VRAM <6 GB): Vulkan path is correct for RDNA 2/3 iGPUs" -ForegroundColor DarkGray
    }
}

if ($amdDGpu -and $InstallServer) {
    Write-Host ""
    Write-Host "  Ollama's Vulkan backend has known correctness issues on RDNA 4 (gfx1201)." -ForegroundColor Yellow
    Write-Host "  The llama.cpp-direct path uses native ROCm 7 kernels via lemonade-sdk." -ForegroundColor Yellow
    if ([System.Environment]::UserInteractive -and $Host.UI.RawUI) {
        $amdChoice = Read-Host "  Install llama-server instead of Ollama? [Y/n] (default Y)"
        $amdChoice = $amdChoice.Trim().ToLower()
        if (-not $amdChoice -or $amdChoice -eq "y" -or $amdChoice -eq "yes") {
            $useLlamacpp = $true
        }
    } else {
        # Non-interactive on an AMD dGPU: pick the correct path by default.
        $useLlamacpp = $true
    }
    if ($useLlamacpp) {
        Write-Host "  Chose llama.cpp-direct path (no Ollama install)." -ForegroundColor Green
    } else {
        Write-Host "  Keeping Ollama+Vulkan. If outputs look wrong, rerun and choose Y." -ForegroundColor Yellow
    }
}

if ($amdGpu -and -not $useLlamacpp) {
    Write-Host "  Setting Vulkan environment variables (recommended for AMD)..."
    [System.Environment]::SetEnvironmentVariable("OLLAMA_VULKAN", "1", "User")
    [System.Environment]::SetEnvironmentVariable("GGML_VK_VISIBLE_DEVICES", "0", "User")
    $env:OLLAMA_VULKAN = "1"
    $env:GGML_VK_VISIBLE_DEVICES = "0"
    Write-Host "  OLLAMA_VULKAN=1 and GGML_VK_VISIBLE_DEVICES=0 set as persistent User env vars"
} elseif (-not $amdGpu) {
    Write-Host "  No AMD GPU detected (NVIDIA/Intel will be auto-detected by Ollama)"
}

# ── Phase 6: Inference engine (Ollama or llama.cpp-direct) ────────────────

Write-Host ""
if ($useLlamacpp) {
    Write-Host "[6/9] Setting up llama.cpp-direct (lemonade $LemonadeTag)..." -ForegroundColor Yellow
} else {
    Write-Host "[6/9] Setting up Ollama..." -ForegroundColor Yellow
}

if (-not $InstallServer -and $InstallClient) {
    Write-Host "  Client mode: skipping local inference install (inference happens on remote server)" -ForegroundColor Green
    Write-Host "  If you want a local fallback, install later with: winget install Ollama.Ollama" -ForegroundColor DarkGray
} elseif ($useLlamacpp) {
    # ── llama.cpp-direct branch ────────────────────────────────────────────
    if (-not (Test-Path $LlamacppDir)) {
        New-Item -ItemType Directory -Path $LlamacppDir -Force | Out-Null
    }
    if (-not (Test-Path $ModelsDir)) {
        New-Item -ItemType Directory -Path $ModelsDir -Force | Out-Null
    }

    $zipName = "llama-$LemonadeTag-windows-rocm-gfx120X-x64.zip"
    $zipUrl  = "https://github.com/lemonade-sdk/llamacpp-rocm/releases/download/$LemonadeTag/$zipName"
    $zipPath = "$env:TEMP\$zipName"

    $serverExeCheck = Get-ChildItem -Path $LlamacppDir -Recurse -Filter "llama-server.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($serverExeCheck) {
        Write-Host "  llama-server.exe already present at $($serverExeCheck.FullName)" -ForegroundColor Green
    } else {
        Write-Host "  Downloading $zipName (~400 MB)..."
        try {
            
            Invoke-WebRequest -Uri $zipUrl -OutFile $zipPath -UseBasicParsing
            
        } catch {
            Write-Host "  ERROR: Download failed from $zipUrl" -ForegroundColor Red
            Write-Host "  Verify the release tag exists at https://github.com/lemonade-sdk/llamacpp-rocm/releases" -ForegroundColor Yellow
            Write-Host "  or override with: `$env:TOKENPAL_LEMONADE_TAG = '<tag>'" -ForegroundColor Yellow
            exit 1
        }
        Write-Host "  Extracting to $LlamacppDir..."
        Expand-Archive -Path $zipPath -DestinationPath $LlamacppDir -Force
        Remove-Item $zipPath -Force -ErrorAction SilentlyContinue
    }

    $serverExe = Get-ChildItem -Path $LlamacppDir -Recurse -Filter "llama-server.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $serverExe) {
        Write-Host "  ERROR: llama-server.exe not found after extract." -ForegroundColor Red
        exit 1
    }
    Write-Host "  llama-server: $($serverExe.FullName)" -ForegroundColor Green

    # GGUF auto-download. Tiers match docs/amd-dgpu-setup.md (verified on
    # apollyon 9070 XT 2026-04-15). All from unsloth HF repos.
    $existingGguf = Get-ChildItem -Path $ModelsDir -Filter "*.gguf" -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($existingGguf) {
        Write-Host "  GGUF already present: $($existingGguf.Name)" -ForegroundColor Green
        $script:LlamacppGgufPath = $existingGguf.FullName
    } else {
        # Pick model by VRAM tier
        if ($amdVramGB -ge 24) {
            $ggufRepo = "unsloth/gemma-4-26B-A4B-it-GGUF"
            $ggufFile = "gemma-4-26B-A4B-it-Q4_K_M.gguf"
            Write-Host "  ${amdVramGB} GB VRAM: pulling 26B MoE Q4_K_M (~17 GB on-card)" -ForegroundColor Cyan
        } elseif ($amdVramGB -ge 12) {
            $ggufRepo = "unsloth/gemma-4-26B-A4B-it-GGUF"
            $ggufFile = "gemma-4-26B-A4B-it-UD-IQ3_S.gguf"
            Write-Host "  ${amdVramGB} GB VRAM: pulling 26B MoE IQ3_S (~13.5 GB on-card, proven on 9070 XT)" -ForegroundColor Cyan
        } elseif ($amdVramGB -ge 6) {
            $ggufRepo = "unsloth/gemma-4-E4B-it-GGUF"
            $ggufFile = "gemma-4-E4B-it-Q4_K_M.gguf"
            Write-Host "  ${amdVramGB} GB VRAM: pulling E4B dense Q4_K_M (~5 GB on-card)" -ForegroundColor Cyan
        } else {
            $ggufRepo = "unsloth/gemma-4-E2B-it-GGUF"
            $ggufFile = "gemma-4-E2B-it-Q4_K_M.gguf"
            Write-Host "  ${amdVramGB} GB VRAM: pulling E2B dense Q4_K_M (~2.5 GB on-card)" -ForegroundColor Cyan
        }

        $ggufUrl = "https://huggingface.co/$ggufRepo/resolve/main/$ggufFile"
        $ggufDest = "$ModelsDir\$ggufFile"

        # Let user confirm or override
        if ([System.Environment]::UserInteractive -and $Host.UI.RawUI) {
            $ggufChoice = Read-Host "  Pull $ggufFile? [Y/n/other filename]"
            $ggufChoice = $ggufChoice.Trim()
            if ($ggufChoice -eq "n" -or $ggufChoice -eq "N") {
                $ggufFile = ""
            } elseif ($ggufChoice -and $ggufChoice -ne "y" -and $ggufChoice -ne "Y") {
                $ggufFile = $ggufChoice
                $ggufUrl = "https://huggingface.co/$ggufRepo/resolve/main/$ggufFile"
                $ggufDest = "$ModelsDir\$ggufFile"
            }
        }

        if ($ggufFile) {
            Write-Host "  Downloading $ggufFile (this may take several minutes)..."
            try {
                Invoke-WebRequest -Uri $ggufUrl -OutFile $ggufDest -UseBasicParsing
                Write-Host "  GGUF downloaded: $ggufDest" -ForegroundColor Green
                $script:LlamacppGgufPath = $ggufDest
            } catch {
                Write-Host "  ERROR: Download failed from $ggufUrl" -ForegroundColor Red
                Write-Host "  Download manually and place in $ModelsDir" -ForegroundColor Yellow
                Write-Host "  See docs/amd-dgpu-setup.md for alternatives." -ForegroundColor Yellow
                $script:LlamacppGgufPath = ""
            }
        } else {
            Write-Host "  Skipping GGUF download. Drop a .gguf into $ModelsDir before launching." -ForegroundColor Yellow
            $script:LlamacppGgufPath = ""
        }
    }

    # Firewall rule for port 11434 (llama-server binds the same port Ollama
    # uses so the TokenPal client proxy is byte-transparent).
    $llmRule = "TokenPal llama-server (TCP 11434)"
    if (-not (Get-NetFirewallRule -DisplayName $llmRule -ErrorAction SilentlyContinue)) {
        try {
            New-NetFirewallRule -DisplayName $llmRule `
                -Direction Inbound -Protocol TCP -LocalPort 11434 `
                -Action Allow -Profile Private -ErrorAction Stop | Out-Null
            Write-Host "  Firewall rule added for TCP 11434" -ForegroundColor Green
        } catch {
            Write-Host "  WARNING: Could not add firewall rule for 11434 (need admin)." -ForegroundColor Yellow
        }
    }
    $script:LlamacppServerExe = $serverExe.FullName
} else {

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
        # Try AMD via registry qwMemorySize (Win32_VideoController.AdapterRAM
        # is a UINT32, capped at 4GB — useless on modern cards). The registry
        # key is the same source GPU-Z uses.
        if ($vramGB -eq 0 -and $amdGpu) {
            $gpuClass = "HKLM:\SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
            $maxBytes = 0L
            if (Test-Path $gpuClass) {
                Get-ChildItem $gpuClass -ErrorAction SilentlyContinue | ForEach-Object {
                    $props = Get-ItemProperty $_.PSPath -ErrorAction SilentlyContinue
                    if ($props -and $props.ProviderName -match "AMD|Advanced Micro|ATI") {
                        $qwm = $props."HardwareInformation.qwMemorySize"
                        if ($qwm -and [int64]$qwm -gt $maxBytes) { $maxBytes = [int64]$qwm }
                    }
                }
            }
            if ($maxBytes -gt 0) {
                $vramGB = [math]::Floor($maxBytes / 1GB)
                Write-Host "  AMD GPU detected with ~${vramGB}GB VRAM" -ForegroundColor Cyan
            }
        }
        # Final fallback: system RAM (no GPU VRAM detected)
        if ($vramGB -eq 0) {
            $vramGB = [math]::Floor((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB)
            Write-Host "  No discrete GPU VRAM detected — using system RAM (${vramGB}GB) for model recommendation" -ForegroundColor Yellow
        }

        # Tiers. For reasoning models (deepseek-r1, qwq), override via TOKENPAL_MODEL.
        # TokenPal strips think tags, so reasoning models fit /ask better than observation quips.
        if ($vramGB -ge 48) {
            $Recommended = "llama3.3:70b"
            Write-Host "  Detected ~${vramGB}GB, recommending llama3.3:70b (70B, best quality)" -ForegroundColor Cyan
        } elseif ($vramGB -ge 32) {
            $Recommended = "gemma4:26b-a4b-it-q8_0"
            Write-Host "  Detected ~${vramGB}GB, recommending gemma4:26b-a4b-it-q8_0 (26B Q8, ~28GB). qwen2.5:32b also fits." -ForegroundColor Cyan
        } elseif ($vramGB -ge 20) {
            $Recommended = "gemma4:26b"
            Write-Host "  Detected ~${vramGB}GB, recommending gemma4:26b (26B, ~19GB, fits entirely in VRAM)" -ForegroundColor Cyan
        } elseif ($vramGB -ge 6) {
            $Recommended = "gemma4"
            Write-Host "  Detected ~${vramGB}GB, recommending gemma4 (9B, solid default)" -ForegroundColor Cyan
        } else {
            $Recommended = "gemma2:2b"
            Write-Host "  Detected ~${vramGB}GB, recommending gemma2:2b (2B, fits small VRAM)" -ForegroundColor Cyan
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

}  # end client-mode skip for Phase 6

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

    # start-*.bat: branch on inference engine
    if ($useLlamacpp) {
        $batPath = "$RepoDir\start-llamaserver.bat"
        $llamaExe = $script:LlamacppServerExe
        $ggufPath = if ($script:LlamacppGgufPath) { $script:LlamacppGgufPath } else { "$ModelsDir\PUT_YOUR_MODEL_HERE.gguf" }
        @"
@echo off
cd /d $RepoDir

rem llama-server binds 11434 so TokenPal's proxy is byte-transparent.
rem Flags (all verified on 9070 XT, see docs/amd-dgpu-setup.md):
rem   -ngl 99       offload all layers to GPU
rem   -c 8192       8k context (default 72k eats VRAM on MoE models)
rem   --no-mmap     force full VRAM load, predictable memory accounting
rem   --jinja       use model's built-in chat template
rem   --reasoning off  route all tokens to content, not reasoning_content
netstat -ano | findstr ":11434" | findstr "LISTENING" >nul
if errorlevel 1 (
    echo Starting llama-server...
    start "" /B "$llamaExe" -m "$ggufPath" --host 0.0.0.0 --port 11434 -ngl 99 -c 8192 --no-mmap --jinja --reasoning off
    timeout /t 8 /nobreak >nul
) else (
    echo llama-server is already running.
)

call .venv\Scripts\activate.bat
tokenpal-server --host 0.0.0.0 --port $Port
pause
"@ | Set-Content -Path $batPath -Encoding ASCII
        Write-Host "  Created $batPath" -ForegroundColor Green
        if (-not $script:LlamacppGgufPath) {
            Write-Host "  NOTE: drop a GGUF into $ModelsDir and edit the -m path in $batPath before launching." -ForegroundColor Yellow
        }
    } else {
        $vulkanLine = if ($amdGpu) { "set OLLAMA_VULKAN=1" } else { "rem set OLLAMA_VULKAN=1  (uncomment for AMD GPU)" }
        $batPath = "$RepoDir\start-server.bat"
        @"
@echo off
cd /d $RepoDir
$vulkanLine
set OLLAMA_KEEP_ALIVE=24h

rem Only launch ollama if it isn't already serving on 11434.
rem Second 'ollama serve' would fail to bind the port and spam a
rem harmless but confusing TCP error.
netstat -ano | findstr ":11434" | findstr "LISTENING" >nul
if errorlevel 1 (
    echo Starting Ollama...
    start "" /B "$ollamaExe" serve
    timeout /t 5 /nobreak >nul
) else (
    echo Ollama is already running.
)

call .venv\Scripts\activate.bat
tokenpal-server --host 0.0.0.0 --port $Port
pause
"@ | Set-Content -Path $batPath -Encoding ASCII
        Write-Host "  Created $batPath" -ForegroundColor Green
    }

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

# If the user picked the llama.cpp-direct path, write [llm] inference_engine.
# Use tomli_w from the venv so we don't corrupt comment-heavy TOML by
# string-patching. Also flip [llm] model_path so the backend knows which
# GGUF llama-server is serving (informational; llama-server binds the model).
if ($useLlamacpp -and (Test-Path $configPath)) {
    $pyScript = @"
import pathlib, sys, tomllib
import tomli_w
path, gguf = sys.argv[1], sys.argv[2]
p = pathlib.Path(path)
data = tomllib.loads(p.read_text())
data.setdefault('llm', {})['inference_engine'] = 'llamacpp'
if gguf:
    data['llm']['model_path'] = gguf
p.write_text(tomli_w.dumps(data))
"@
    $ggufArg = if ($script:LlamacppGgufPath) { $script:LlamacppGgufPath } else { "" }
    try {
        $pyScript | & "$VenvDir\Scripts\python.exe" - $configPath $ggufArg 2>$null
        Write-Host "  Wrote [llm] inference_engine = llamacpp to config.toml" -ForegroundColor Green
    } catch {
        Write-Host "  WARNING: Could not set inference_engine. Add [llm] inference_engine = `"llamacpp`" manually." -ForegroundColor Yellow
    }
}

# Client mode: ask which remote inference server to connect to
if ($InstallClient -and -not $InstallServer -and (Test-Path $configPath)) {
    $serverTarget = $env:TOKENPAL_SERVER
    if (-not $serverTarget -and [System.Environment]::UserInteractive -and $Host.UI.RawUI) {
        Write-Host ""
        Write-Host "  Client mode: which inference server should the buddy connect to?" -ForegroundColor Cyan
        Write-Host "  Enter hostname (becomes http://host:8585/v1) or full URL," -ForegroundColor DarkGray
        Write-Host "  or leave blank to configure later via /server switch." -ForegroundColor DarkGray
        $serverTarget = Read-Host "  Server"
    }
    $serverTarget = ($serverTarget -replace '\s', '')
    if ($serverTarget) {
        if ($serverTarget -match '^https?://') {
            $serverUrl = $serverTarget.TrimEnd('/')
        } else {
            $serverUrl = "http://${serverTarget}:8585/v1"
        }
        if (-not ($serverUrl -match '/v1$')) {
            $serverUrl = "$serverUrl/v1"
        }
        $pyScript = @"
import json, pathlib, sys, tomllib, urllib.request
import tomli_w
path, url = sys.argv[1], sys.argv[2]
p = pathlib.Path(path)
data = tomllib.loads(p.read_text())
data.setdefault('llm', {})['api_url'] = url

base = url.rstrip('/')
if base.endswith('/v1'):
    base = base[:-3]

models = []
for endpoint, key in (('/api/v1/models/list', None), ('/api/tags', 'models')):
    try:
        with urllib.request.urlopen(f'{base}{endpoint}', timeout=5) as resp:
            parsed = json.loads(resp.read().decode('utf-8'))
        models = parsed if key is None else parsed.get(key, [])
        if models:
            break
    except Exception:
        continue

if models:
    models.sort(key=lambda m: m.get('size') or 0, reverse=True)
    picked = models[0]
    data['llm']['model_name'] = picked['name']
    size_gb = (picked.get('size') or 0) / 1e9
    print(f"{picked['name']}|{size_gb:.1f}")

p.write_text(tomli_w.dumps(data))
"@
        try {
            $modelSuggested = ($pyScript | & "$VenvDir\Scripts\python.exe" - $configPath $serverUrl 2>$null) | Out-String
            $modelSuggested = $modelSuggested.Trim()
            Write-Host "  Client points at $serverUrl" -ForegroundColor Green
            if ($modelSuggested) {
                $parts = $modelSuggested.Split('|', 2)
                $mName = $parts[0]
                $mSize = if ($parts.Length -gt 1) { $parts[1] } else { '?' }
                Write-Host "  Detected server model: $mName ($mSize GB) saved to [llm] model_name" -ForegroundColor Green
            } else {
                Write-Host "  WARNING: Could not list models on $serverUrl. Keeping default model_name." -ForegroundColor Yellow
                Write-Host "  Run /model list after launch to see what the server has." -ForegroundColor Yellow
            }
        } catch {
            Write-Host "  WARNING: Could not write api_url. Edit $configPath manually ([llm] api_url)." -ForegroundColor Yellow
        }
    } else {
        Write-Host "  WARNING: No server set. The buddy will fail on launch." -ForegroundColor Yellow
        Write-Host "  Fix by editing [llm] api_url in $configPath or running /server switch." -ForegroundColor Yellow
    }
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
    $serverBat = if ($useLlamacpp) { "start-llamaserver.bat" } else { "start-server.bat" }
    Write-Host "  Start server:  $RepoDir\$serverBat"
    Write-Host ""
    Write-Host "  Client config.toml:" -ForegroundColor White
    Write-Host "    [llm]"
    Write-Host "    api_url = `"http://${hostname}:${Port}/v1`""
    Write-Host ""
}

Write-Host "  Config:  $RepoDir\config.toml"
Write-Host "  Logs:    ~\.tokenpal\logs\tokenpal.log"
Write-Host ""

# ── Offer to launch ────────────────────────────────────────────────────────
if ([System.Environment]::UserInteractive -and $Host.UI.RawUI) {
    $launchChoice = ""
    if ($InstallClient -and -not $InstallServer) {
        $ans = Read-Host "Launch TokenPal now? [y/N]"
        if ($ans -match '^(y|yes)$') { $launchChoice = "client" }
    } elseif ($InstallServer -and -not $InstallClient) {
        $ans = Read-Host "Launch tokenpal-server now? [y/N]"
        if ($ans -match '^(y|yes)$') { $launchChoice = "server" }
    } elseif ($InstallClient -and $InstallServer) {
        $ans = Read-Host "Launch now? [c]lient / [s]erver / [n]one"
        switch -Regex ($ans) {
            '^(c|client)$' { $launchChoice = "client" }
            '^(s|server)$' { $launchChoice = "server" }
        }
    }

    $clientExe = "$VenvDir\Scripts\tokenpal.exe"
    $serverExe = "$VenvDir\Scripts\tokenpal-server.exe"
    if ($launchChoice -eq "client" -and (Test-Path $clientExe)) {
        Write-Host "Launching tokenpal..." -ForegroundColor Cyan
        & $clientExe
        exit $LASTEXITCODE
    } elseif ($launchChoice -eq "server" -and (Test-Path $serverExe)) {
        # On the llamacpp path, start llama-server first (the bat does this +
        # launches tokenpal-server). On the Ollama path, Ollama is already
        # running from Phase 6 so we can launch the proxy directly.
        if ($useLlamacpp -and (Test-Path $batPath)) {
            Write-Host "Launching via $batPath (Ctrl-C to stop)..." -ForegroundColor Cyan
            & cmd.exe /c $batPath
            exit $LASTEXITCODE
        }
        Write-Host "Launching tokenpal-server (Ctrl-C to stop)..." -ForegroundColor Cyan
        & $serverExe --host 0.0.0.0
        exit $LASTEXITCODE
    }
}
