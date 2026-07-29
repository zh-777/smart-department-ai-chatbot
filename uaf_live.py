import re
import time
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup


HEADERS = {"User-Agent": "UAF-Live-FYP/1.0 (Educational)"}
TIMEOUT = 8
DOMAIN = "uaf.edu.pk"

MAX_PAGES_PER_QUERY = 5
MAX_LINKS_PER_PAGE = 10
DELAY = 0.1

FACULTY_DIRECTORY_URL = "https://web.uaf.edu.pk/FacultyProfile/Directory"
UNDERGRAD_ADM_URL = "https://web.uaf.edu.pk/Contents/admissions/un/adm_overview.html"
POSTGRAD_ADM_URL = "https://web.uaf.edu.pk/Contents/admissions/post/adm_overview.html"
EVENTS_URL = "https://web.uaf.edu.pk/News/AllNews"
CAMPUS_NEWS_URL = "https://web.uaf.edu.pk/CampusNews/ViewCampusNews"

# Map departments to the correct UAF faculty directory pages
DEPARTMENT_DIRECTORY_MAP = {
    "computer science": "https://web.uaf.edu.pk/FacultyProfile/Directory?id=21",
    "cs": "https://web.uaf.edu.pk/FacultyProfile/Directory?id=21",
    "chemistry": "https://web.uaf.edu.pk/FacultyProfile/Directory?id=22",
    "biochemistry": "https://web.uaf.edu.pk/FacultyProfile/Directory?id=23",
    "physics": "https://web.uaf.edu.pk/FacultyProfile/Directory?id=26",
    "mathematics": "https://web.uaf.edu.pk/FacultyProfile/Directory?id=27",
    "statistics": "https://web.uaf.edu.pk/FacultyProfile/Directory?id=27",
    "botany": "https://web.uaf.edu.pk/FacultyProfile/Directory?id=20",
    "zoology": "https://web.uaf.edu.pk/FacultyProfile/Directory?id=28",

    "agronomy": "https://web.uaf.edu.pk/FacultyProfile/Directory?id=4",
    "entomology": "https://web.uaf.edu.pk/FacultyProfile/Directory?id=5",
    "plant pathology": "https://web.uaf.edu.pk/FacultyProfile/Directory?id=7",
    "plant breeding": "https://web.uaf.edu.pk/FacultyProfile/Directory?id=8",
    "forestry": "https://web.uaf.edu.pk/FacultyProfile/Directory?id=9",

    "anatomy": "https://web.uaf.edu.pk/FacultyProfile/Directory?id=12",
    "pharmacy": "https://web.uaf.edu.pk/FacultyProfile/Directory?id=45",
}


SEED_URLS = [
    FACULTY_DIRECTORY_URL,
    UNDERGRAD_ADM_URL,
    POSTGRAD_ADM_URL,
    EVENTS_URL,
    CAMPUS_NEWS_URL,
    "https://web.uaf.edu.pk/",
]

EVENT_KEYWORDS = [
    "event", "events", "seminar", "workshop", "conference",
    "webinar", "notice", "notices", "news", "activity", "activities"
]

MERIT_KEYWORDS = [
    "merit", "merit list", "merit lists", "selected candidates",
    "entry test", "result", "results"
]

ADMISSION_KEYWORDS = [
    "admission", "admissions", "apply", "apply online",
    "prospectus", "fee structure", "advertisement"
]


def _allowed(url: str) -> bool:
    try:
        host = urlparse(url).netloc.lower()
        return DOMAIN in host
    except Exception:
        return False


def _clean_text(s: str) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()


def _normalize_text(s: str) -> str:
    return _clean_text(s).lower()


def _fetch_html(url: str) -> str:
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    ctype = r.headers.get("content-type", "")
    if "text/html" not in ctype:
        return ""
    return r.text


def _extract_text_and_links(html: str, base_url: str):
    soup = BeautifulSoup(html, "lxml")

    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()

    text = _clean_text(soup.get_text(" "))
    links = []

    for a in soup.select("a[href]"):
        href = (a.get("href") or "").strip()
        if not href:
            continue

        full = urljoin(base_url, href).split("#")[0]
        if _allowed(full):
            links.append(full)

    seen = set()
    uniq = []
    for x in links:
        if x not in seen:
            seen.add(x)
            uniq.append(x)

    return text, uniq


def _query_program_terms(query: str):
    q = _normalize_text(query)

    mapping = {
        "computer science": ["computer science", "cs", "bs cs", "bs-cs", "department of computer science"],
        "chemistry": ["chemistry", "department of chemistry"],
        "physics": ["physics", "department of physics"],
        "mathematics": ["mathematics", "math", "department of mathematics"],
        "statistics": ["statistics", "department of mathematics and statistics"],
        "botany": ["botany", "department of botany"],
        "zoology": ["zoology", "department of zoology"],
        "biochemistry": ["biochemistry", "department of biochemistry"],
        "agronomy": ["agronomy", "department of agronomy"],
    }

    found = []
    for canonical, vals in mapping.items():
        if any(v in q for v in vals):
            found.append(canonical)

    return list(dict.fromkeys(found))


def _detect_department(query: str) -> str:
    terms = _query_program_terms(query)
    return terms[0] if terms else ""


def _resolve_department_url(query: str) -> str:
    dept = _detect_department(query)
    if not dept:
        return FACULTY_DIRECTORY_URL
    return DEPARTMENT_DIRECTORY_MAP.get(dept, FACULTY_DIRECTORY_URL)


def _looks_like_person_name(text: str) -> bool:
    s = _clean_text(text)
    if not s:
        return False
    if len(s) < 6 or len(s) > 80:
        return False

    patterns = [
        r"^(Dr|Prof|Professor|Mr|Mrs|Ms)\.?\s+[A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+){0,5}$",
        r"^[A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+){1,5}$",
    ]
    return any(re.match(p, s) for p in patterns)


def _extract_staff_particulars_text(page_text: str) -> str:
    text = page_text.replace("\r", "\n")
    text = re.sub(r"\n+", "\n", text)

    m = re.search(r"Staff Particulars(.*)$", text, flags=re.I | re.S)
    if not m:
        return text

    section = m.group(1)
    stop_markers = ["copyright", "all rights reserved", "quick links", "home"]
    lower_section = section.lower()
    stop_index = len(section)

    for marker in stop_markers:
        idx = lower_section.find(marker)
        if idx != -1:
            stop_index = min(stop_index, idx)

    return section[:stop_index].strip()


def _extract_teacher_entries_from_staff_text(staff_text: str, source_url: str, dept_label: str):
    lines = [_clean_text(x) for x in staff_text.split("\n")]
    lines = [x for x in lines if x]

    teachers = []
    i = 0
    while i < len(lines):
        line = lines[i]

        if _looks_like_person_name(line):
            name = line
            designation = ""
            phone = ""
            email = ""

            j = i + 1
            while j < len(lines):
                current = lines[j]

                if _looks_like_person_name(current):
                    break

                if current in {
                    "Professor", "Associate Professor", "Assistant Professor",
                    "Lecturer", "Instructor", "Adjunct Faculty", "Chairman", "Dean"
                }:
                    designation = current

                if "@" in current and "." in current:
                    email = current

                if re.search(r"\+?\d[\d\s().-]{6,}", current):
                    phone = current

                j += 1

            teachers.append({
                "name": name,
                "designation": designation,
                "department": dept_label,
                "phone": phone,
                "email": email,
                "url": source_url
            })

            i = j
        else:
            i += 1

    seen = set()
    out = []
    for t in teachers:
        key = t["name"].strip().lower()
        if key not in seen:
            seen.add(key)
            out.append(t)

    return out


def live_extract_department_teachers(query: str):
    try:
        dept = _detect_department(query)
        url = _resolve_department_url(query)
        html = _fetch_html(url)
        if not html:
            return []

        soup = BeautifulSoup(html, "lxml")
        for tag in soup(["script", "style", "noscript", "svg"]):
            tag.decompose()

        page_text = soup.get_text("\n")
        staff_text = _extract_staff_particulars_text(page_text)
        dept_label = dept.title() if dept else ""
        teachers = _extract_teacher_entries_from_staff_text(staff_text, url, dept_label)

        return teachers[:40]
    except Exception:
        return []


def _extract_link_items(url: str, mode: str):
    """
    mode: 'admission', 'merit', 'events', 'news'
    Returns list of dict items.
    """
    try:
        html = _fetch_html(url)
        if not html:
            return []

        soup = BeautifulSoup(html, "lxml")
        for tag in soup(["script", "style", "noscript", "svg"]):
            tag.decompose()

        items = []

        if mode in {"admission", "merit"}:
            # admission pages are mostly clean link lists
            for a in soup.select("a[href]"):
                title = _clean_text(a.get_text(" ", strip=True))
                href = (a.get("href") or "").strip()
                if not title or not href:
                    continue

                full = urljoin(url, href)
                title_l = title.lower()

                if mode == "admission":
                    if not any(k in title_l for k in ADMISSION_KEYWORDS):
                        continue
                elif mode == "merit":
                    if not any(k in title_l for k in MERIT_KEYWORDS):
                        continue

                items.append({
                    "title": title,
                    "date": "",
                    "snippet": "",
                    "url": full
                })

        elif mode == "events":
            # UAF News page has repeated cards/headings + nearby metadata
            text = soup.get_text("\n")
            text = re.sub(r"\n+", "\n", text)

            lines = [_clean_text(x) for x in text.split("\n") if _clean_text(x)]
            for i, line in enumerate(lines):
                if len(line) < 8:
                    continue

                line_l = line.lower()
                if any(k in line_l for k in EVENT_KEYWORDS) or (i + 1 < len(lines) and re.match(r"\d{1,2}/\d{1,2}/\d{4}", lines[i + 1])):
                    date = ""
                    snippet = ""
                    if i + 1 < len(lines) and re.match(r"\d{1,2}/\d{1,2}/\d{4}", lines[i + 1]):
                        date = lines[i + 1]
                    if i + 2 < len(lines):
                        snippet = lines[i + 2][:220]

                    items.append({
                        "title": line[:160],
                        "date": date,
                        "snippet": snippet,
                        "url": url
                    })

        elif mode == "news":
            for a in soup.select("a[href]"):
                title = _clean_text(a.get_text(" ", strip=True))
                href = (a.get("href") or "").strip()
                if not title or len(title) < 8:
                    continue
                full = urljoin(url, href)
                items.append({
                    "title": title[:160],
                    "date": "",
                    "snippet": "",
                    "url": full
                })

        # dedupe
        seen = set()
        out = []
        for it in items:
            key = (it["title"].lower(), it["url"])
            if key not in seen:
                seen.add(key)
                out.append(it)

        return out[:20]
    except Exception:
        return []


def live_extract_uaf_events(query: str):
    return _extract_link_items(EVENTS_URL, "events")[:10]


def live_extract_uaf_notices(query: str):
    return _extract_link_items(CAMPUS_NEWS_URL, "news")[:10]


def live_extract_undergrad_admissions(query: str):
    return _extract_link_items(UNDERGRAD_ADM_URL, "admission")[:20]


def live_extract_postgrad_admissions(query: str):
    return _extract_link_items(POSTGRAD_ADM_URL, "admission")[:20]


def live_extract_undergrad_merit_lists(query: str):
    return _extract_link_items(UNDERGRAD_ADM_URL, "merit")[:20]


def live_extract_postgrad_merit_lists(query: str):
    return _extract_link_items(POSTGRAD_ADM_URL, "merit")[:20]


def _score_text_for_query(text: str, query: str) -> float:
    q_words = re.findall(r"[a-zA-Z0-9]+", query.lower())
    if not q_words:
        return 0.0

    t = text.lower()
    score = 0.0

    for w in q_words:
        if len(w) >= 3:
            score += t.count(w) * 2

    if "admission" in query.lower() and "admission" in t:
        score += 10
    if "merit" in query.lower() and "merit" in t:
        score += 10
    if any(k in query.lower() for k in EVENT_KEYWORDS) and any(k in t for k in EVENT_KEYWORDS):
        score += 12

    score += min(len(text) / 3000.0, 1.5)
    return score


def _best_snippet(text: str, query: str, max_chars: int = 1200) -> str:
    chunks = re.split(r"(?<=[.!?])\s+|\s{2,}", text)
    q_words = [w for w in re.findall(r"[a-zA-Z0-9]+", query.lower()) if len(w) >= 3]

    scored = []
    for chunk in chunks:
        c = chunk.strip()
        if len(c) < 40:
            continue

        cl = c.lower()
        score = 0

        for w in q_words:
            if w in cl:
                score += 2

        if "admission" in cl:
            score += 4
        if "merit" in cl:
            score += 4
        if any(k in cl for k in EVENT_KEYWORDS):
            score += 5

        if score > 0:
            scored.append((score, c))

    scored.sort(key=lambda x: x[0], reverse=True)

    out = []
    total = 0
    for _, s in scored[:8]:
        if total + len(s) > max_chars:
            break
        out.append(s)
        total += len(s) + 1

    if out:
        return " ".join(out)

    return text[:max_chars]


def live_retrieve_uaf(query: str):
    queue = list(dict.fromkeys(SEED_URLS))
    seen = set()
    candidates = []

    while queue and len(seen) < MAX_PAGES_PER_QUERY:
        url = queue.pop(0)

        if url in seen:
            continue
        if not _allowed(url):
            continue

        seen.add(url)

        try:
            html = _fetch_html(url)
            if not html:
                continue

            text, links = _extract_text_and_links(html, url)

            if len(text) < 200:
                continue

            score = _score_text_for_query(text, query)
            if score > 0:
                candidates.append({
                    "url": url,
                    "text": text,
                    "score": score
                })

            for lk in links[:MAX_LINKS_PER_PAGE]:
                if lk not in seen and lk not in queue:
                    queue.append(lk)

            time.sleep(DELAY)

        except Exception:
            continue

    candidates.sort(key=lambda x: x["score"], reverse=True)
    best = candidates[:2]

    snippets = []
    for c in best:
        snippets.append({
            "url": c["url"],
            "snippet": _best_snippet(c["text"], query, max_chars=1200)
        })

    return snippets