# Build a distributable zip (no secrets, no .venv).
param(
    [string]$OutDir = ""
)

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    python -m venv (Join-Path $Root ".venv")
    & $Python -m pip install -r (Join-Path $Root "requirements.txt")
}

$dest = if ($OutDir) { $OutDir } else { Join-Path $Root "dist" }
& $Python -c "from pathlib import Path; from bot.core.pack_dist import build_suggest_bot_zip; print(build_suggest_bot_zip(Path(r'$dest')))"
