# TokenPal one-liner installer for Windows.
#
#   iwr -useb https://raw.githubusercontent.com/smabe/TokenPal/main/install.ps1 | iex
#
# Downloads the Windows platform installer and runs it.

$ErrorActionPreference = "Stop"

$repo   = if ($env:TOKENPAL_REPO)   { $env:TOKENPAL_REPO }   else { "smabe/TokenPal" }
$branch = if ($env:TOKENPAL_BRANCH) { $env:TOKENPAL_BRANCH } else { "main" }
$url    = "https://raw.githubusercontent.com/$repo/$branch/scripts/install-windows.ps1"

$tmp = Join-Path $env:TEMP "tokenpal-install-$([guid]::NewGuid().ToString('N')).ps1"

Write-Host "==> Downloading install-windows.ps1 from $repo@$branch..."
Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $tmp

try {
    & powershell.exe -ExecutionPolicy Bypass -File $tmp @args
    exit $LASTEXITCODE
} finally {
    Remove-Item -Force -ErrorAction SilentlyContinue $tmp
}
