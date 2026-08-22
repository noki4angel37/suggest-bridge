# Install suggest host agent for THIS package folder (standalone).
# Usage:
#   .\install-agent.ps1
#   .\install-agent.ps1 -Uninstall
#
# After install:
#   1) Copy .env.example to .env and fill tokens
#   2) Optional: share HOST_SYNC_DIR between admin PCs for /host handover
#   3) Start now: .\run-agent.ps1   (or log off/on)
#   4) In Telegram / Discord: /host

param(
    [string]$TaskName = "suggest-bridge-agent",
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"
$BotDir = $PSScriptRoot
$AgentScript = Join-Path $BotDir "run-agent.ps1"
$EnvExample = Join-Path $BotDir ".env.example"
$EnvFile = Join-Path $BotDir ".env"
$SyncDir = Join-Path $env:LOCALAPPDATA "suggest-host-sync"

function Remove-TaskIfExists([string]$Name) {
    Unregister-ScheduledTask -TaskName $Name -Confirm:$false -ErrorAction SilentlyContinue
}

if ($Uninstall) {
    Remove-TaskIfExists $TaskName
    Remove-TaskIfExists "suggest-agent"
    Write-Host "Uninstalled task $TaskName"
    exit 0
}

if (-not (Test-Path -LiteralPath $AgentScript)) {
    throw "Missing $AgentScript — unpack the full suggest-bridge package."
}

& $AgentScript -InstallDeps
if ($LASTEXITCODE -ne 0 -and $null -ne $LASTEXITCODE) {
    Write-Warning "run-agent -InstallDeps exited $LASTEXITCODE (continuing)"
}

New-Item -ItemType Directory -Force -Path $SyncDir | Out-Null
foreach ($sub in @("registry", "commands", "acks")) {
    New-Item -ItemType Directory -Force -Path (Join-Path $SyncDir $sub) | Out-Null
}

if (-not (Test-Path -LiteralPath $EnvFile) -and (Test-Path -LiteralPath $EnvExample)) {
    Copy-Item -LiteralPath $EnvExample -Destination $EnvFile
    Write-Host "Created $EnvFile from .env.example — fill BOT_TOKEN / DISCORD_TOKEN / ADMIN_IDS"
}

Remove-TaskIfExists $TaskName
Remove-TaskIfExists "suggest-agent"
$arg = "-NoProfile -ExecutionPolicy Bypass -File `"$AgentScript`""
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $arg
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal | Out-Null

Write-Host ""
Write-Host "Installed suggest-bridge agent." -ForegroundColor Green
Write-Host "  Package:  $BotDir"
Write-Host "  Task:     $TaskName (AtLogOn)"
Write-Host "  Sync dir: $SyncDir  (optional multi-PC; set HOST_SYNC_DIR)"
Write-Host "  Env:      $EnvFile"
Write-Host ""
Write-Host "Next:"
Write-Host "  1) Edit .env (tokens and admin ids)"
Write-Host "  2) Optional: share sync folder between admin PCs (Syncthing, NAS, etc.)"
Write-Host "  3) Start agent now: .\run-agent.ps1"
Write-Host "  4) Telegram: /host   Discord: /host in moderation channel"
