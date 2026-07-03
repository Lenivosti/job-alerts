# Job Alert Bot

A personal automation tool that monitors career pages of 56 companies daily and sends new relevant job postings to Telegram.

## How it works

GitHub Actions runs a Python script once a day on a schedule. The script checks career pages across six source types — Ashby, Greenhouse, Lever, Teamtailor, Breezy, and Workable public APIs, plus direct HTML parsing for company sites without a known ATS. Only postings matching target keywords (travel, event, coordinator, project manager) are collected. New postings — ones not seen in previous runs — are sent as a Telegram message via a bot. Already-seen postings are stored in `seen_jobs.json` to avoid duplicates.

## Stack

- Python 3.11 · `requests` · `beautifulsoup4`
- GitHub Actions (scheduled via cron)
- Telegram Bot API

## Configuration

Add two repository secrets in Settings → Secrets and variables → Actions:
- `TELEGRAM_BOT_TOKEN` — token from @BotFather
- `TELEGRAM_CHAT_ID` — your personal chat ID

To add or remove companies, edit `companies.json`.
