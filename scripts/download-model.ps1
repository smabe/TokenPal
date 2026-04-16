#Requires -Version 5.1
<#
.SYNOPSIS
Download a GGUF model for use with llama-server on the llamacpp path.

.DESCRIPTION
Interactive model picker for TokenPal's llama.cpp-direct backend. Lists
known-good models by VRAM tier, downloads via curl.exe, and optionally
updates start-llamaserver.bat + config.toml to point at the new model.

.EXAMPLE
.\download-model.ps1              # interactive picker
.\download-model.ps1 -Pick 3      # download choice #3 directly
.\download-model.ps1 -Url "https://huggingface.co/..." -Name "my-model.gguf"
#>

param(
    [int]$Pick,
    [string]$Url,
    [string]$Name
)

$ErrorActionPreference = "Stop"

$ModelsDir = "$env:LOCALAPPDATA\TokenPal\models"
$RepoDir   = if ($env:TOKENPAL_DIR) { $env:TOKENPAL_DIR } else { "$env:USERPROFILE\tokenpal" }

if (-not (Test-Path $ModelsDir)) {
    New-Item -ItemType Directory -Path $ModelsDir -Force | Out-Null
}

# ── Model catalog ──────────────────────────────────────────────────────────
# All verified to work with lemonade-sdk llamacpp-rocm on gfx120X.
# Format: @{ Name; Repo; File; SizeGB (on-card estimate); Description }

$Models = @(
    @{
        Name = "gemma-4 26B MoE IQ3_S"
        Repo = "unsloth/gemma-4-26B-A4B-it-GGUF"
        File = "gemma-4-26B-A4B-it-UD-IQ3_S.gguf"
        SizeGB = 13.5
        Desc = "26B MoE, ~102 tok/s on 9070 XT. Best comedy. Current default."
    },
    @{
        Name = "gemma-4 26B MoE Q3_K_M"
        Repo = "unsloth/gemma-4-26B-A4B-it-GGUF"
        File = "gemma-4-26B-A4B-it-Q3_K_M.gguf"
        SizeGB = 14.0
        Desc = "26B MoE, slightly better quality than IQ3_S. Tight fit on 16 GB."
    },
    @{
        Name = "gemma-4 E4B dense Q4_K_M"
        Repo = "unsloth/gemma-4-E4B-it-GGUF"
        File = "gemma-4-E4B-it-Q4_K_M.gguf"
        SizeGB = 5.0
        Desc = "7.5B dense, ~106 tok/s. Fast and correct. Good for snappy quips."
    },
    @{
        Name = "gemma-4 E4B dense Q8_0"
        Repo = "unsloth/gemma-4-E4B-it-GGUF"
        File = "gemma-4-E4B-it-Q8_0.gguf"
        SizeGB = 9.0
        Desc = "7.5B dense at Q8 quality. You have the VRAM, why not."
    },
    @{
        Name = "gemma-4 E2B dense Q4_K_M"
        Repo = "unsloth/gemma-4-E2B-it-GGUF"
        File = "gemma-4-E2B-it-Q4_K_M.gguf"
        SizeGB = 2.5
        Desc = "2.6B dense. Tiny and fast. Good baseline to compare against."
    },
    @{
        Name = "Qwen3 14B Q4_K_M"
        Repo = "unsloth/Qwen3-14B-GGUF"
        File = "Qwen3-14B-Q4_K_M.gguf"
        SizeGB = 9.0
        Desc = "14B, strong reasoning + tool calling. Needs --jinja flag."
    },
    @{
        Name = "Llama 3.1 8B Instruct Q8_0"
        Repo = "bartowski/Meta-Llama-3.1-8B-Instruct-GGUF"
        File = "Meta-Llama-3.1-8B-Instruct-Q8_0.gguf"
        SizeGB = 8.5
        Desc = "8B at full Q8. Rock-solid all-rounder."
    },
    @{
        Name = "Phi-4 Q4_K_M"
        Repo = "bartowski/phi-4-GGUF"
        File = "phi-4-Q4_K_M.gguf"
        SizeGB = 8.0
        Desc = "14B from Microsoft. Punches above weight for commentary."
    }
)

# ── Custom URL mode ────────────────────────────────────────────────────────

if ($Url) {
    if (-not $Name) {
        $Name = [System.IO.Path]::GetFileName($Url)
    }
    $dest = "$ModelsDir\$Name"
    Write-Host "Downloading $Name..." -ForegroundColor Cyan
    & curl.exe -fSL -o $dest --connect-timeout 15 -H "Connection: close" $Url
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Download failed." -ForegroundColor Red
        exit 1
    }
    Write-Host "Saved to $dest" -ForegroundColor Green
    # Fall through to config update
    $chosenFile = $Name
    $chosenDest = $dest
} else {

    # ── Interactive picker ─────────────────────────────────────────────────

    Write-Host ""
    Write-Host "TokenPal Model Downloader" -ForegroundColor Cyan
    Write-Host "=========================" -ForegroundColor Cyan
    Write-Host "Models dir: $ModelsDir"
    Write-Host ""

    # Show existing models
    $existing = Get-ChildItem -Path $ModelsDir -Filter "*.gguf" -ErrorAction SilentlyContinue
    if ($existing) {
        Write-Host "Already downloaded:" -ForegroundColor Green
        foreach ($f in $existing) {
            $sizeGB = [math]::Round($f.Length / 1GB, 1)
            Write-Host "  - $($f.Name) (${sizeGB} GB)" -ForegroundColor DarkGreen
        }
        Write-Host ""
    }

    Write-Host "Available models:" -ForegroundColor Yellow
    Write-Host ""
    for ($i = 0; $i -lt $Models.Count; $i++) {
        $m = $Models[$i]
        $num = $i + 1
        $tag = ""
        $existingMatch = $existing | Where-Object { $_.Name -eq $m.File }
        if ($existingMatch) { $tag = " [already downloaded]" }
        $fits = if ($m.SizeGB -le 14) { "" } else { " [TIGHT FIT]" }
        Write-Host "  [$num] $($m.Name) (~$($m.SizeGB) GB)${fits}${tag}" -ForegroundColor White
        Write-Host "      $($m.Desc)" -ForegroundColor DarkGray
    }
    Write-Host ""

    if (-not $Pick) {
        $input = Read-Host "Pick a number (1-$($Models.Count)), or 'q' to quit"
        if ($input -eq 'q' -or $input -eq 'Q' -or -not $input) {
            Write-Host "Cancelled." -ForegroundColor DarkGray
            exit 0
        }
        $Pick = [int]$input
    }

    if ($Pick -lt 1 -or $Pick -gt $Models.Count) {
        Write-Host "Invalid choice: $Pick" -ForegroundColor Red
        exit 1
    }

    $chosen = $Models[$Pick - 1]
    $chosenFile = $chosen.File
    $chosenDest = "$ModelsDir\$chosenFile"
    $chosenUrl = "https://huggingface.co/$($chosen.Repo)/resolve/main/$chosenFile"

    # Skip if already exists
    if (Test-Path $chosenDest) {
        $sizeGB = [math]::Round((Get-Item $chosenDest).Length / 1GB, 1)
        Write-Host "$chosenFile already exists (${sizeGB} GB). Skipping download." -ForegroundColor Green
    } else {
        Write-Host ""
        Write-Host "Downloading $chosenFile (~$($chosen.SizeGB) GB)..." -ForegroundColor Cyan
        Write-Host "From: $chosenUrl" -ForegroundColor DarkGray
        Write-Host ""
        & curl.exe -fSL -o $chosenDest --connect-timeout 15 -H "Connection: close" $chosenUrl
        if ($LASTEXITCODE -ne 0) {
            Write-Host "Download failed." -ForegroundColor Red
            Write-Host "URL: $chosenUrl" -ForegroundColor Yellow
            exit 1
        }
        $sizeGB = [math]::Round((Get-Item $chosenDest).Length / 1GB, 1)
        Write-Host "Downloaded: $chosenDest (${sizeGB} GB)" -ForegroundColor Green
    }
}

# ── Update config + bat ────────────────────────────────────────────────────

$modelStem = [System.IO.Path]::GetFileNameWithoutExtension($chosenFile)

Write-Host ""
$update = Read-Host "Update config.toml + start-llamaserver.bat to use $modelStem? [Y/n]"
if (-not $update -or $update -match '^(y|yes)$') {

    # Update config.toml via Python (preserves comments)
    $configPath = "$RepoDir\config.toml"
    $VenvDir = "$RepoDir\.venv"
    if ((Test-Path $configPath) -and (Test-Path "$VenvDir\Scripts\python.exe")) {
        $pyScript = @"
import pathlib, sys, tomllib
import tomli_w
path, gguf, stem = sys.argv[1], sys.argv[2], sys.argv[3]
p = pathlib.Path(path)
data = tomllib.loads(p.read_text())
data.setdefault('llm', {})['model_path'] = gguf
data['llm']['model_name'] = stem
p.write_text(tomli_w.dumps(data))
"@
        try {
            $pyScript | & "$VenvDir\Scripts\python.exe" - $configPath $chosenDest $modelStem 2>$null
            Write-Host "  config.toml updated: model_name = $modelStem" -ForegroundColor Green
        } catch {
            Write-Host "  WARNING: Could not update config.toml. Edit manually." -ForegroundColor Yellow
        }
    } else {
        Write-Host "  config.toml or venv not found at $RepoDir. Edit manually." -ForegroundColor Yellow
    }

    # Update start-llamaserver.bat
    $batPath = "$RepoDir\start-llamaserver.bat"
    if (Test-Path $batPath) {
        $content = Get-Content $batPath -Raw
        $newContent = $content -replace '(?<=-m\s")[^"]*(?=")', $chosenDest
        if ($newContent -ne $content) {
            $newContent | Set-Content $batPath -Encoding ASCII -NoNewline
            Write-Host "  start-llamaserver.bat updated: -m $chosenDest" -ForegroundColor Green
        } else {
            Write-Host "  start-llamaserver.bat: could not find -m path to replace. Edit manually." -ForegroundColor Yellow
        }
    } else {
        Write-Host "  start-llamaserver.bat not found at $RepoDir. Edit manually." -ForegroundColor Yellow
    }

    Write-Host ""
    Write-Host "Restart llama-server to load the new model." -ForegroundColor Cyan
}

Write-Host ""
Write-Host "Done." -ForegroundColor Green
