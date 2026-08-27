# Sync docs/wiki/ → GitHub Wiki (suggest-bridge.wiki.git)
# Requires: git, gh auth (or HTTPS credentials), wiki already bootstrapped
# (first Home page created once via GitHub UI if .wiki.git does not exist yet).

param(
    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path,
    [string]$WikiUrl = "https://github.com/noki4angel37/suggest-bridge.wiki.git"
)

$ErrorActionPreference = "Stop"
$src = Join-Path $RepoRoot "docs\wiki"
if (-not (Test-Path (Join-Path $src "Home.md"))) {
    throw "Missing $src\Home.md"
}

$gh = Get-Command gh -ErrorAction SilentlyContinue
if (-not $gh -and (Test-Path "C:\Program Files\GitHub CLI\gh.exe")) {
    $gh = "C:\Program Files\GitHub CLI\gh.exe"
}

$cloneUrl = $WikiUrl
if ($gh) {
    $token = & $gh auth token 2>$null
    if ($token) {
        $cloneUrl = "https://x-access-token:${token}@github.com/noki4angel37/suggest-bridge.wiki.git"
    }
}

$tmp = Join-Path ([System.IO.Path]::GetTempPath()) ("suggest-bridge-wiki-" + [guid]::NewGuid().ToString("n"))
try {
    git clone $cloneUrl $tmp
    Get-ChildItem $tmp -File | Remove-Item -Force
    Copy-Item -Force (Join-Path $src "*") $tmp
    Push-Location $tmp
    git add -A
    $status = git status --porcelain
    if (-not $status) {
        Write-Host "Wiki already up to date."
        return
    }
    git -c user.email="noki4angel37@users.noreply.github.com" -c user.name="noki4angel37" `
        commit -m "docs: sync operator wiki from docs/wiki"
    git push origin HEAD:master
    Write-Host "Published: https://github.com/noki4angel37/suggest-bridge/wiki"
}
finally {
    Pop-Location -ErrorAction SilentlyContinue
    if (Test-Path $tmp) { Remove-Item -Recurse -Force $tmp }
}
