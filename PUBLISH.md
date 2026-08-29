# Publish to GitHub

Release zip: `dist/suggest-bot-*.zip` (CI on tag `v*.*.*`).

## One-time login

```powershell
gh auth login
# GitHub.com → HTTPS → Login with browser
```

## Create repo and push

```powershell
cd suggest-bridge
gh repo create suggest-bridge --public --source=. --remote=origin --push --description "Self-hosted Telegram-Discord community platform: suggest, bridge, moderation modules (SB_MODULES)"
git push origin v0.1.0
gh release create v0.1.0 dist/suggest-bot-*.zip --title "v0.1.0" --notes-file CHANGELOG.md
```

## GitHub topics (optional)

`suggest-bot`, `telegram-bot`, `discord-bot`, `self-hosted`, `python`, `aiogram`, `community`, `community-platform`, `discord-telegram-bridge`

```powershell
gh api --method PUT repos/OWNER/suggest-bridge/topics `
  -H "Accept: application/vnd.github+json" `
  -f names='["suggest-bot","telegram-bot","discord-bot","self-hosted","python","aiogram","community","community-platform","discord-telegram-bridge"]'
```
