# Publish to GitHub

The repo is ready at `c:\!projects\suggest-bridge` (committed, tagged `v0.1.0`).

Release zip: `dist/suggest-bot-20260822-1526.zip`

## One-time login

```powershell
gh auth login
# GitHub.com → HTTPS → Login with browser
```

## Create repo and push

```powershell
cd c:\!projects\suggest-bridge
gh repo create suggest-bridge --public --source=. --remote=origin --push --description "Self-hosted Telegram Discord suggest bot"
git push origin v0.1.0
gh release create v0.1.0 dist/suggest-bot-*.zip --title "v0.1.0" --notes-file CHANGELOG.md
```

## GitHub topics (optional)

`suggest-bot`, `telegram-bot`, `discord-bot`, `self-hosted`, `python`, `aiogram`, `community`
