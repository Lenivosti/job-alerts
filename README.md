# Job Alert Bot

A personal automation tool that monitors the career pages of 120+ companies every day and sends new relevant job postings to Telegram.

## How it works

GitHub Actions runs a Python script once a day on a schedule. The script reads career pages through eight ATS integrations (Ashby, Greenhouse, Lever, Teamtailor, Breezy, Workable, Workday and Eightfold public APIs), plus direct HTML parsing for company sites without a known ATS. Postings are matched by whole words against target role titles (operations, project and people/workplace coordination), and an exclusion list drops near-miss titles such as DevOps, sales or revenue operations and technical project management. HTML results are filtered to drop navigation links and marketing pages. Only postings not seen in previous runs are sent to Telegram; seen postings are stored in `seen_jobs.json`.

## Stack

- Python 3.11 · `requests` · `beautifulsoup4`
- GitHub Actions (scheduled via cron)
- Telegram Bot API

## Configuration

Add two repository secrets in Settings → Secrets and variables → Actions:
- `TELEGRAM_BOT_TOKEN` — token from @BotFather
- `TELEGRAM_CHAT_ID` — your personal chat ID

To add or remove companies or tune keywords, edit `companies.json`; exclusion words live in `exclude_keywords.json`.
