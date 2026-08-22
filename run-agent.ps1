# Always-on suggest host agent (no Telegram getUpdates).
# Usage (from this folder):
#   .\run-agent.ps1
#   .\run-agent.ps1 -InstallDeps
#
# Env: HOST_ID (default hostname:username), HOST_SYNC_DIR, .env for tokens.

param(
    [switch]$InstallDeps
)

$ErrorActionPreference = "Stop"
$BotDir = $PSScriptRoot
$VenvDir = Join-Path $BotDir ".venv"
$Python = Join-Path $VenvDir "Scripts\python.exe"
$Req = Join-Path $BotDir "requirements.txt"

if (-not $env:HOST_ID -or -not $env:HOST_ID.Trim()) {
    $user = if ($env:USERNAME) { $env:USERNAME } else { "user" }
    $env:HOST_ID = "$($env:COMPUTERNAME):$user"
}

if (-not $env:HOST_SYNC_DIR -or -not $env:HOST_SYNC_DIR.Trim()) {
    $env:HOST_SYNC_DIR = Join-Path $env:LOCALAPPDATA "suggest-host-sync"
}
New-Item -ItemType Directory -Force -Path $env:HOST_SYNC_DIR | Out-Null
foreach ($sub in @("registry", "commands", "acks")) {
    New-Item -ItemType Directory -Force -Path (Join-Path $env:HOST_SYNC_DIR $sub) | Out-Null
}

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw "Python not found in PATH. Install Python 3.11+ and retry."
}

if ($InstallDeps -or -not (Test-Path -LiteralPath $Python)) {
    python -m venv $VenvDir
    & $Python -m pip install --upgrade pip
    & $Python -m pip install -r $Req
}

Write-Host "suggest-bridge agent HOST_ID=$($env:HOST_ID) sync=$($env:HOST_SYNC_DIR)"
Push-Location $BotDir
try {
    & $Python -m bot.agent
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
