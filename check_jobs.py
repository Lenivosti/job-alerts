import json
import os
import re
import hashlib
import time
import requests
from bs4 import BeautifulSoup
import xml.etree.ElementTree as ET

STATE_FILE = "seen_jobs.json"
COMPANIES_FILE = "companies.json"

TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; JobAlertBot/1.0; +https://github.com)"}


def load_json(path, default):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    # Telegram limits messages to 4096 characters; split if needed
    chunks = [text[i:i + 3500] for i in range(0, len(text), 3500)] or [text]
    for chunk in chunks:
        resp = requests.post(url, data={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": chunk,
            "parse_mode": "HTML",
            "disable_web_page_preview": False
        }, timeout=20)
        if not resp.ok:
            print(f"[TELEGRAM ERROR] {resp.status_code}: {resp.text}")


def matches_keywords(text, keywords):
    text_lower = text.lower()
    return any(kw.lower() in text_lower for kw in keywords)


# Признаки того, что ссылка ведёт на КОНКРЕТНУЮ вакансию, а не на раздел сайта.
# Хотя бы один из этих фрагментов должен быть в URL html-ссылки.
JOB_URL_HINTS = ["/job", "/jobs/", "/vacanc", "/position", "/opening", "/careers/", "/career/", "/apply", "/p/", "-vacancy", "gh_jid", "lever.co", "ashbyhq", "workable", "teamtailor", "breezy", "/o/"]

# Тексты-ссылки, которые почти всегда являются пунктами меню/навигации, а не вакансиями.
NAV_TEXT_BLOCKLIST = {
    "events", "event", "travel", "mobility", "mobility & relocation",
    "careers", "career", "about", "about us", "blog", "news", "company",
    "team", "our team", "culture", "life", "benefits", "perks", "press",
    "contact", "home", "jobs", "open roles", "all jobs", "vacancies",
}


def looks_like_job_link(text, url):
    """Для html-источников: True, только если ссылка похожа на конкретную вакансию,
    а её текст не выглядит как навигационный пункт меню."""
    text_norm = text.strip().lower()
    # Отсекаем короткие навигационные подписи ("Events", "Travel" и т.п.)
    if text_norm in NAV_TEXT_BLOCKLIST:
        return False
    # Отсекаем маркетинговые страницы-сравнения ("Onde vs. Atom Mobility")
    if " vs. " in text_norm or " vs " in text_norm or text_norm.startswith("vs.") or text_norm.startswith("vs "):
        return False
    url_lower = url.lower()
    has_job_hint = any(h in url_lower for h in JOB_URL_HINTS)
    if not has_job_hint:
        return False
    return True


def make_id(*parts):
    raw = "|".join(parts)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


# ---------- Источники: каждый возвращает список (title, url) ----------

def fetch_ashby(slug):
    url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    jobs = []
    for job in data.get("jobs", []):
        title = job.get("title", "")
        link = job.get("jobUrl") or job.get("applyUrl") or ""
        jobs.append((title, link))
    return jobs


def fetch_greenhouse(slug):
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    jobs = []
    for job in data.get("jobs", []):
        title = job.get("title", "")
        link = job.get("absolute_url", "")
        jobs.append((title, link))
    return jobs


def fetch_lever(slug):
    url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    jobs = []
    for job in data:
        title = job.get("text", "")
        link = job.get("hostedUrl", "")
        jobs.append((title, link))
    return jobs


def fetch_teamtailor(slug):
    url = f"https://{slug}.teamtailor.com/jobs.rss"
    resp = requests.get(url, headers={**HEADERS, "Accept": "application/rss+xml, application/xml, text/xml"}, timeout=20)
    resp.raise_for_status()
    jobs = []
    root = ET.fromstring(resp.content)
    for item in root.findall(".//item"):
        title_el = item.find("title")
        link_el = item.find("link")
        title = title_el.text if title_el is not None else ""
        link = link_el.text if link_el is not None else ""
        jobs.append((title, link))
    return jobs


def fetch_breezy(slug):
    url = f"https://{slug}.breezy.hr/json"
    resp = requests.get(url, params={"verbose": "true"}, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    jobs = []
    for job in data:
        title = job.get("name", "")
        friendly_id = job.get("friendly_id", "")
        link = f"https://{slug}.breezy.hr/p/{friendly_id}" if friendly_id else f"https://{slug}.breezy.hr"
        jobs.append((title, link))
    return jobs


def fetch_workable(slug):
    url = f"https://apply.workable.com/api/v1/widget/accounts/{slug}"
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    jobs = []
    for job in data.get("jobs", []):
        title = job.get("title", "")
        shortcode = job.get("shortcode", "")
        link = f"https://apply.workable.com/{slug}/j/{shortcode}" if shortcode else f"https://apply.workable.com/{slug}/"
        jobs.append((title, link))
    return jobs


def fetch_html(url):
    headers = {
        **HEADERS,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }
    resp = requests.get(url, headers=headers, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    jobs = []
    for a in soup.find_all("a", href=True):
        text = a.get_text(strip=True)
        href = a["href"]
        if text:
            full_url = href if href.startswith("http") else url.rstrip("/") + "/" + href.lstrip("/")
            jobs.append((text, full_url))
    return jobs


FETCHERS = {
    "ashby": lambda c: fetch_ashby(c["slug"]),
    "greenhouse": lambda c: fetch_greenhouse(c["slug"]),
    "lever": lambda c: fetch_lever(c["slug"]),
    "teamtailor": lambda c: fetch_teamtailor(c["slug"]),
    "breezy": lambda c: fetch_breezy(c["slug"]),
    "workable": lambda c: fetch_workable(c["slug"]),
    "html": lambda c: fetch_html(c["url"]),
}


def main():
    companies = load_json(COMPANIES_FILE, [])
    seen = load_json(STATE_FILE, {})

    new_findings = []
    errors = []

    for company in companies:
        name = company["name"]
        ctype = company.get("type", "html")
        keywords = company.get("keywords", [])
        fetcher = FETCHERS.get(ctype)

        if fetcher is None:
            errors.append(f"{name}: unknown type '{ctype}'")
            continue

        try:
            postings = fetcher(company)
        except Exception as e:
            errors.append(f"{name} ({ctype}): {e}")
            print(f"[ERROR] {name}: {e}")
            continue

        for title, link in postings:
            if not title or not matches_keywords(title, keywords):
                continue

            # Для html-источников дополнительно отсекаем навигационные ссылки
            # (пункты меню "Events", "Travel" и т.п.), оставляя только реальные вакансии.
            if ctype == "html" and not looks_like_job_link(title, link):
                continue

            job_id = make_id(name, title, link)
            if job_id in seen:
                continue

            seen[job_id] = True
            new_findings.append((name, title, link))

        time.sleep(1)  # вежливая пауза между запросами к разным сайтам

    if new_findings:
        message_lines = ["🔔 <b>Новые вакансии:</b>\n"]
        for name, title, link in new_findings:
            message_lines.append(f"<b>{name}</b>: {title}\n{link}\n")
        send_telegram("\n".join(message_lines))
        print(f"Found {len(new_findings)} new postings, notification sent.")
    else:
        print("No new postings today.")

    if errors:
        print("Errors during run:")
        for err in errors:
            print(f"  - {err}")
        # Раскомментируй следующую строку, если хочешь получать уведомления и об ошибках:
        # send_telegram("⚠️ Ошибки при проверке:\n" + "\n".join(errors))

    save_json(STATE_FILE, seen)


if __name__ == "__main__":
    main()
