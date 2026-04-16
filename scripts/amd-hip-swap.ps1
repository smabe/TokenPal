#Requires -Version 5.1
<#
.SYNOPSIS
Swap Ollama's bundled HIP 6 runtime with the system HIP 7 SDK. Intended for
RDNA 3 (gfx1100/gfx1101/gfx1102) where Ollama bundles outdated HIP 6 kernels.

DOES NOT WORK ON RDNA 4 (gfx1200/gfx1201). Tested 2026-04-15: HIP 7 DLLs have
the kernels, but Ollama's HSA discovery still hangs / returns empty on gfx1201
regardless of which HIP runtime is loaded. Root cause tracked upstream in
ROCm#5812 and ollama#9812 / #10430 / #12573 / #13908 / #14686 / #14927.
For RDNA 4, use docs/amd-dgpu-setup.md (llama.cpp-direct via lemonade-sdk).

.DESCRIPTION
Ollama ships HIP 6 DLLs without gfx1200/1201 kernels. This script backs up the
bundled DLLs, then copies HIP 7 SDK DLLs over them (renaming amdhip64_7.dll to
amdhip64_6.dll to satisfy Ollama's import table), and replaces the rocblas
Tensile library with HIP 7's version (which includes gfx1201 kernels).

.PARAMETER Restore
Undo the swap: restore from the backup created on first run.

.PARAMETER OllamaPath
Override Ollama install path (default: %LOCALAPPDATA%\Programs\Ollama).

.PARAMETER HipPath
Override HIP SDK path (default: C:\Program Files\AMD\ROCm\7.1).

.EXAMPLE
# Swap in HIP 7 (creates backup):
.\amd-hip-swap.ps1

# Restore original HIP 6:
.\amd-hip-swap.ps1 -Restore
#>

[CmdletBinding()]
param(
    [switch]$Restore,
    [string]$OllamaPath = "$env:LOCALAPPDATA\Programs\Ollama",
    [string]$HipPath    = "C:\Program Files\AMD\ROCm\7.1"
)

$ErrorActionPreference = 'Stop'

$rocmDir   = Join-Path $OllamaPath 'lib\ollama\rocm'
$backupDir = Join-Path $OllamaPath 'lib\ollama\rocm.hip6.bak'
$hipBin    = Join-Path $HipPath 'bin'

function Assert-Path($path, $label) {
    if (-not (Test-Path $path)) {
        throw "$label not found: $path"
    }
}

function Stop-Ollama {
    Get-Process ollama -ErrorAction SilentlyContinue | ForEach-Object {
        Write-Host "  Stopping ollama.exe (pid $($_.Id))..." -ForegroundColor Yellow
        Stop-Process -Id $_.Id -Force
    }
    Start-Sleep -Seconds 2
}

Assert-Path $rocmDir "Ollama ROCm directory"
Stop-Ollama

if ($Restore) {
    Write-Host "=== Restoring HIP 6 from backup ===" -ForegroundColor Cyan
    Assert-Path $backupDir "Backup directory (nothing to restore from)"

    Remove-Item $rocmDir -Recurse -Force
    Move-Item $backupDir $rocmDir
    Write-Host "  Restored. Ollama is back on bundled HIP 6." -ForegroundColor Green
    exit 0
}

# --- Swap path ---

Assert-Path $hipBin "HIP SDK bin"
Assert-Path (Join-Path $hipBin 'amdhip64_7.dll') "HIP 7 runtime DLL"

if (Test-Path $backupDir) {
    throw "Backup already exists at $backupDir. Run with -Restore first, or delete the backup."
}

Write-Host "=== Swapping Ollama HIP 6 -> system HIP 7 ===" -ForegroundColor Cyan
Write-Host "  Ollama ROCm : $rocmDir"
Write-Host "  HIP 7 SDK   : $HipPath"
Write-Host ""

# 1. Backup
Write-Host "1. Backing up to $backupDir"
Copy-Item $rocmDir $backupDir -Recurse -Force

# 2. Replace core DLLs
$coreDlls = @{
    'amdhip64_7.dll'  = 'amdhip64_6.dll'   # rename to match Ollama's import
    'rocblas.dll'     = 'rocblas.dll'
    'hipblas.dll'     = 'hipblas.dll'
    'amd_comgr_2.dll' = 'amd_comgr_2.dll'
}

Write-Host "2. Copying HIP 7 DLLs (amdhip64_7 renamed to amdhip64_6)"
foreach ($src in $coreDlls.Keys) {
    $srcPath = Join-Path $hipBin $src
    $dstPath = Join-Path $rocmDir $coreDlls[$src]
    if (-not (Test-Path $srcPath)) {
        Write-Warning "  skip: $src not found in HIP SDK"
        continue
    }
    Copy-Item $srcPath $dstPath -Force
    Write-Host "   $src -> $($coreDlls[$src])"
}

# 3. Replace rocblas kernel library (this is where gfx1201 kernels live)
$hipRocblasLib = Join-Path $hipBin 'rocblas\library'
$ollRocblasLib = Join-Path $rocmDir 'rocblas\library'
if (Test-Path $hipRocblasLib) {
    Write-Host "3. Replacing rocblas/library with HIP 7 (includes gfx1201 kernels)"
    if (Test-Path $ollRocblasLib) { Remove-Item $ollRocblasLib -Recurse -Force }
    Copy-Item $hipRocblasLib $ollRocblasLib -Recurse -Force
} else {
    Write-Warning "  HIP 7 rocblas/library not found at $hipRocblasLib -- kernels not swapped"
}

Write-Host ""
Write-Host "Done. Restart Ollama and check server.log for library=rocm." -ForegroundColor Green
Write-Host "If it crashes or produces wrong output: .\amd-hip-swap.ps1 -Restore" -ForegroundColor Yellow
