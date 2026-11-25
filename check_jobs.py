import json
import os
import re
import hashlib
import time
import requests
from bs4 import BeautifulSoup
import xml.etree.ElementTree as ET
import html
from functools import lru_cache

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


def _send_one(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    resp = requests.post(url, data={
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }, timeout=20)
    if not resp.ok:
        print(f"[TELEGRAM ERROR] {resp.status_code}: {resp.text}")
    return resp.ok


def send_findings(findings):
    """Шлёт вакансии пачками, разрезая только между вакансиями, а не посреди тега.
    Названия экранируются, чтобы символы < > & в них не ломали разметку.
    Возвращает вакансии, которые реально дошли до Telegram."""
    header = "🔔 <b>Новые вакансии:</b>\n\n"
    delivered, batch, batch_items = [], header, []
    for item in findings:
        name, title, link = item
        entry = f"<b>{html.escape(name)}</b>: {html.escape(title)}\n{html.escape(link)}\n\n"
        if batch_items and len(batch) + len(entry) > 3500:
            if _send_one(batch):
                delivered.extend(batch_items)
            batch, batch_items = header, []
            time.sleep(1)
        batch += entry
        batch_items.append(item)
    if batch_items and _send_one(batch):
        delivered.extend(batch_items)
    return delivered


EXCLUDE_FILE = "exclude_keywords.json"


def _normalize(text):
    """Нижний регистр, "&" приравниваем к "and"."""
    return text.lower().replace("&", " and ")


@lru_cache(maxsize=None)
def _phrase_pattern(phrase):
    """Ищем фразу по целым словам: "vp" не сработает внутри "mvp",
    "it operations" — внутри "credit operations". Между словами допускаем
    пробелы, дефисы, слэши и т.п. ("Operations-Manager", "People & Culture")."""
    words = re.findall(r"[a-z0-9]+", _normalize(phrase))
    if not words:
        return None
    return re.compile(r"(?<![a-z0-9])" + r"[^a-z0-9]+".join(map(re.escape, words)) + r"(?![a-z0-9])")


def _contains_any(text, phrases):
    norm = _normalize(text)
    for phrase in phrases:
        pattern = _phrase_pattern(phrase)
        if pattern and pattern.search(norm):
            return True
    return False


def matches_keywords(text, keywords):
    return _contains_any(text, keywords)


def is_excluded(text, excludes):
    """Слово-исключение в названии побеждает любое ключевое слово."""
    return _contains_any(text, excludes)


JOB_URL_HINTS = ["/job", "/jobs/", "/vacanc", "/position", "/opening", "/careers/", "/career/", "/apply", "/p/", "-vacancy", "gh_jid", "lever.co", "ashbyhq", "workable", "teamtailor", "breezy", "/o/"]

NAV_TEXT_BLOCKLIST = {
    "events", "event", "travel", "mobility", "mobility & relocation",
    "careers", "career", "about", "about us", "blog", "news", "company",
    "team", "our team", "culture", "life", "benefits", "perks", "press",
    "contact", "home", "jobs", "open roles", "all jobs", "vacancies",
    "visa", "relocation",
}


def looks_like_job_link(text, url):
    """Для html-источников: True, если ссылка похожа на конкретную вакансию.

    Сначала жёстко отсекаем явный мусор (тех. ссылки, короткие пункты
    меню, сравнения "X vs Y"). Дальше — если в URL есть явный признак
    вакансии, пропускаем. Если признака в URL нет, всё равно пропускаем,
    если название состоит из 2-8 слов и не длиннее 80 символов — так
    двухсловные должности вроде "Travel Specialist" не теряются, а
    однословные пункты меню всё ещё отсекаются через NAV_TEXT_BLOCKLIST.
    """
    text_norm = text.strip().lower()
    url_lower = url.lower()

    if "cdn-cgi" in url_lower or "email-protection" in url_lower or url_lower.startswith("mailto:"):
        return False
    if text_norm in NAV_TEXT_BLOCKLIST:
        return False
    if " vs. " in text_norm or " vs " in text_norm or text_norm.startswith("vs.") or text_norm.startswith("vs "):
        return False

    has_job_hint = any(h in url_lower for h in JOB_URL_HINTS)
    if has_job_hint:
        return True

    word_count = len(text_norm.split())
    if 2 <= word_count <= 8 and len(text_norm) <= 80:
        return True

    return False


def make_id(*parts):
    raw = "|".join(parts)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


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


def _get_with_eu_fallback(urls):
    """Европейские аккаунты Greenhouse и Lever живут на отдельном адресе API:
    если основной отвечает 404, пробуем европейский."""
    resp = None
    for url in urls:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        if resp.status_code != 404:
            break
    resp.raise_for_status()
    return resp


def fetch_greenhouse(slug):
    resp = _get_with_eu_fallback([
        f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
        f"https://boards-api.eu.greenhouse.io/v1/boards/{slug}/jobs",
    ])
    data = resp.json()
    jobs = []
    for job in data.get("jobs", []):
        title = job.get("title", "")
        link = job.get("absolute_url", "")
        jobs.append((title, link))
    return jobs


def fetch_lever(slug):
    resp = _get_with_eu_fallback([
        f"https://api.lever.co/v0/postings/{slug}?mode=json",
        f"https://api.eu.lever.co/v0/postings/{slug}?mode=json",
    ])
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


def fetch_workday(company):
    """Workday: сайт сам берёт вакансии из JSON-адреса /wday/cxs/{tenant}/{site}/jobs.
    Отдаёт по 20 штук за запрос, поэтому листаем страницы (до 4000 вакансий)."""
    host, tenant, site = company["host"], company["tenant"], company["site"]
    api = f"https://{host}/wday/cxs/{tenant}/{site}/jobs"
    headers = {**HEADERS, "Content-Type": "application/json", "Accept": "application/json"}
    jobs, offset, limit, total = [], 0, 20, None
    while offset < 4000:
        resp = requests.post(api, json={"appliedFacets": {}, "limit": limit, "offset": offset, "searchText": ""},
                             headers=headers, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        postings = data.get("jobPostings", [])
        for p in postings:
            title = p.get("title", "")
            path = p.get("externalPath", "")
            jobs.append((title, f"https://{host}/en-US/{site}{path}"))
        if total is None:
            # Workday сообщает общее число только на первой странице, дальше присылает 0
            total = data.get("total", 0)
        offset += limit
        if not postings or offset >= total:
            break
        time.sleep(0.3)
    print(f"[INFO] {company['name']} (workday): всего на сайте {total}, прочитано {len(jobs)}")
    return jobs


def _eightfold_positions(data):
    """Достаёт список вакансий из ответа Eightfold (старый и новый формат API)."""
    if isinstance(data, dict):
        if isinstance(data.get("positions"), list):
            return data["positions"]
        inner = data.get("data")
        if isinstance(inner, dict) and isinstance(inner.get("positions"), list):
            return inner["positions"]
    return []


def fetch_eightfold(company):
    """Eightfold (например, jobs.autodesk.com): ищем по каждому ключевому слову,
    как поиск на самом сайте. Пробуем классический API, если он не отвечает — новый."""
    host, domain = company["host"], company["domain"]
    headers = {**HEADERS, "Accept": "application/json"}
    endpoints = [
        (f"https://{host}/api/apply/v2/jobs", {"domain": domain, "start": 0, "num": 50}),
        (f"https://{host}/api/pcsx/search", {"domain": domain, "start": 0}),
    ]
    jobs, seen_ids, working = [], set(), None
    for kw in company.get("keywords", []):
        candidates = [working] if working else endpoints
        for url, base_params in candidates:
            try:
                resp = requests.get(url, params={**base_params, "query": kw}, headers=headers, timeout=20)
                resp.raise_for_status()
                positions = _eightfold_positions(resp.json())
            except Exception:
                if working:
                    raise
                continue
            working = (url, base_params)
            for p in positions:
                pid = str(p.get("id") or p.get("ats_job_id") or p.get("name"))
                if pid in seen_ids:
                    continue
                seen_ids.add(pid)
                title = p.get("name") or p.get("title") or ""
                link = p.get("canonicalPositionUrl") or p.get("positionUrl") or f"/careers/job/{p.get('id', '')}"
                if link.startswith("/"):
                    link = f"https://{host}{link}"
                jobs.append((title, link))
            break
        if not working:
            raise RuntimeError(f"Eightfold API не ответил ни по одному адресу на {host}")
        time.sleep(0.3)
    print(f"[INFO] {company['name']} (eightfold): найдено по ключевым словам {len(jobs)} вакансий")
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
    "workday": lambda c: fetch_workday(c),
    "eightfold": lambda c: fetch_eightfold(c),
    "html": lambda c: fetch_html(c["url"]),
}


def main():
    companies = load_json(COMPANIES_FILE, [])
    seen = load_json(STATE_FILE, {})
    global_excludes = load_json(EXCLUDE_FILE, [])

    new_findings = []
    excluded_count = 0
    errors = []

    for company in companies:
        name = company["name"]
        ctype = company.get("type", "html")
        keywords = company.get("keywords", [])
        excludes = global_excludes + company.get("exclude", [])
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

            if is_excluded(title, excludes):
                excluded_count += 1
                continue

            if ctype == "html" and not looks_like_job_link(title, link):
                continue

            job_id = make_id(name, title, link)
            if job_id in seen:
                continue

            seen[job_id] = True  # временно, чтобы не дублировать внутри прогона
            new_findings.append((name, title, link, job_id))

        time.sleep(1)

    print(f"[INFO] Отсечено словами-исключениями: {excluded_count}")

    if new_findings:
        delivered = send_findings([f[:3] for f in new_findings])
        delivered_set = set(delivered)
        for name, title, link, job_id in new_findings:
            if (name, title, link) not in delivered_set:
                seen.pop(job_id, None)  # не дошло — пришлём в следующий раз
        print(f"Found {len(new_findings)} new postings, delivered to Telegram: {len(delivered)}.")
    else:
        print("No new postings today.")

    if errors:
        print("Errors during run:")
        for err in errors:
            print(f"  - {err}")

    save_json(STATE_FILE, seen)


if __name__ == "__main__":
    main()
