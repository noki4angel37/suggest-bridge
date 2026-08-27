# Prepare .env for deployment: copy template, generate HOST_SYNC_SECRET, set HEALTH_PORT.
# Usage (from repo root): .\scripts\deploy\prepare-env.ps1

$ErrorActionPreference = "Stop"
$Root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
Set-Location $Root

$Example = Join-Path $Root ".env.example"
$EnvFile = Join-Path $Root ".env"

if (-not (Test-Path $Example)) {
    Write-Error ".env.example not found in $Root"
}

if (-not (Test-Path $EnvFile)) {
    Copy-Item $Example $EnvFile
    Write-Host "Created .env from .env.example"
} else {
    Write-Host ".env already exists - updating HOST_SYNC_SECRET / HEALTH_PORT if needed"
}

$content = Get-Content $EnvFile -Raw

if ($content -match '(?m)^HOST_SYNC_SECRET=REPLACE_ME\s*$') {
    $secret = -join ((1..32) | ForEach-Object { "{0:x2}" -f (Get-Random -Maximum 256) })
    $content = $content -replace '(?m)^HOST_SYNC_SECRET=REPLACE_ME\s*$', "HOST_SYNC_SECRET=$secret"
    Write-Host "Generated HOST_SYNC_SECRET"
}

if ($content -notmatch '(?m)^HEALTH_PORT=') {
    $content = $content.TrimEnd() + "`nHEALTH_PORT=8080`n"
    Write-Host "Added HEALTH_PORT=8080"
} elseif ($content -match '(?m)^HEALTH_PORT=\s*$') {
    $content = $content -replace '(?m)^HEALTH_PORT=\s*$', 'HEALTH_PORT=8080'
    Write-Host "Set HEALTH_PORT=8080"
}

[System.IO.File]::WriteAllText($EnvFile, $content.TrimEnd() + "`n")
Write-Host ""
Write-Host "Next: edit .env - BOT_TOKEN, DISCORD_TOKEN, ADMIN_IDS, CHANNEL_ID, OWNER_DISCORD_ID"
Write-Host "Docker: docker compose up -d"
Write-Host "Local:  .\.venv\Scripts\python.exe -m bot.main"
