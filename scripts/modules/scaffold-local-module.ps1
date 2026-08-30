# Creates a minimal SB_MODULES Python file outside the repo.
param(
    [string]$OutDir = (Join-Path $env:USERPROFILE "suggest-bridge-modules"),
    [string]$ClassName = "HelloModule",
    [string]$FileName = "hello_module.py"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$template = Join-Path $repoRoot "examples\local_module_template\hello_module.py"
if (-not (Test-Path $template)) {
    Write-Error "Template not found: $template"
}
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$dest = Join-Path $OutDir $FileName
Copy-Item -Force $template $dest
Write-Host "Created: $dest"
Write-Host ""
Write-Host "Add to .env (use forward slashes or escaped backslashes on Windows):"
Write-Host "SB_MODULES=$dest`:$ClassName"
Write-Host ""
Write-Host "Validate: python -m bot.core.module_loader"
