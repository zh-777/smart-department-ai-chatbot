from flask import Flask, request, jsonify, render_template, Response, session
from uaf_live import (
    live_retrieve_uaf,
    live_extract_department_teachers,
    live_extract_uaf_events,
    live_extract_uaf_notices,
    live_extract_undergrad_admissions,
    live_extract_postgrad_admissions,
    live_extract_undergrad_merit_lists,
    live_extract_postgrad_merit_lists,
    FACULTY_DIRECTORY_URL,
    UNDERGRAD_ADM_URL,
    POSTGRAD_ADM_URL,
    EVENTS_URL,
    CAMPUS_NEWS_URL,
)
import pandas as pd
import re
import json
import requests
from werkzeug.security import check_password_hash
from datetime import datetime
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret-change-me")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

faculty_csv = os.path.join(BASE_DIR, "data", "CS_Department_Faculty.csv")
fee_csv = os.path.join(BASE_DIR, "data", "Approved_Fee_2025-26_parsed.csv")
users_csv = os.path.join(BASE_DIR, "data", "teachers_users.csv")
status_json = os.path.join(BASE_DIR, "data", "teacher_status.json")

OLLAMA_URL = "http://localhost:11434/api/chat"
OLLAMA_MODEL = "qwen2.5:1.5b"

OLLAMA_TIMEOUT_SEC = 30  # Optimized timeout
OLLAMA_NUM_PREDICT_GENERAL = 400
OLLAMA_NUM_PREDICT_LIVE = 300
OLLAMA_NUM_PREDICT_STREAM = 420


# -------------------------------------------------
# DATA LOADING
# -------------------------------------------------

def safe_read_csv(path: str, columns=None):
    if not os.path.exists(path):
        if columns is None:
            return pd.DataFrame()
        return pd.DataFrame(columns=columns)
    try:
        return pd.read_csv(path).fillna("")
    except Exception:
        if columns is None:
            return pd.DataFrame()
        return pd.DataFrame(columns=columns)

def get_faculty_df():
    df = safe_read_csv(CSV_PATH)
    if "Mobile Number" in df.columns and "contact/phone" not in df.columns:
        df.rename(columns={"Mobile Number": "contact/phone"}, inplace=True)
        
    required_faculty_cols = [
        "name", "designation", "floor",
        "office time/student consultation hours",
        "email", "contact/phone", "Office Phone", "Specialization"
    ]
    for col in required_faculty_cols:
        if col not in df.columns:
            df[col] = ""

    df["name_norm"] = df["name"].astype(str).str.lower().str.strip()
    return df

def get_fee_df():
    fee_df = safe_read_csv(FEE_CSV_PATH)
    required_fee_cols = ["degree_program", "first_semester_fee", "subsequent_fee", "total_fee"]
    for col in required_fee_cols:
        if col not in fee_df.columns:
            fee_df[col] = ""

    fee_df["degree_norm"] = fee_df["degree_program"].astype(str).str.lower().str.strip()
    return fee_df

def row_shift(deg: str) -> str:
    d = (deg or "").lower()
    if "(m)" in d or " morning" in d or re.search(r"\bm\b", d):
        return "morning"
    if "(e)" in d or " evening" in d or re.search(r"\be\b", d):
        return "evening"
    return ""


def get_fee_df_with_shift():
    fee_df = get_fee_df()
    fee_df["shift"] = fee_df["degree_program"].astype(str).apply(row_shift)
    return fee_df


# -------------------------------------------------
# OLLAMA HELPERS
# -------------------------------------------------

def ask_llama(system_prompt: str, user_prompt: str, num_predict: int = OLLAMA_NUM_PREDICT_GENERAL) -> str:
    payload = {
        "model": OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "stream": False,
        "options": {
            "temperature": 0.2,
            "top_p": 0.9,
            "num_predict": num_predict
        }
    }

    r = requests.post(OLLAMA_URL, json=payload, timeout=OLLAMA_TIMEOUT_SEC)
    r.raise_for_status()
    data = r.json()
    return (data.get("message", {}) or {}).get("content", "").strip() or "No response."


def ollama_stream(messages, num_predict: int = OLLAMA_NUM_PREDICT_STREAM):
    payload = {
        "model": OLLAMA_MODEL,
        "messages": messages,
        "stream": True,
        "options": {
            "temperature": 0.2,
            "top_p": 0.9,
            "num_predict": num_predict
        }
    }

    with requests.post(OLLAMA_URL, json=payload, stream=True, timeout=OLLAMA_TIMEOUT_SEC) as r:
        r.raise_for_status()
        for line in r.iter_lines(decode_unicode=True):
            if not line:
                continue
            obj = json.loads(line)
            token = obj.get("message", {}).get("content", "")
            if token:
                yield token


def ollama_available() -> bool:
    try:
        r = requests.get("http://localhost:11434/api/tags", timeout=2)
        return r.ok
    except Exception:
        return False


# -------------------------------------------------
# NORMALIZATION / CLASSIFICATION
# -------------------------------------------------

def normalize_text(s: str) -> str:
    return str(s or "").strip().lower()


def get_instant_reply(q: str):
    qn = normalize_text(q)
    qn_simple = re.sub(r"[^\w\s]", "", qn).strip()

    greeting_map = {
        "hi": "Hello! How can I help you today?",
        "hello": "Hello! How can I help you today?",
        "hey": "Hi! How can I help you today?",
        "assalamualaikum": "Walaikum Assalam! How can I help you today?",
        "salam": "Walaikum Assalam! How can I help you today?",
        "thanks": "You're welcome.",
        "thank you": "You're welcome.",
        "ok": "Alright.",
        "bye": "Goodbye.",
        "goodbye": "Goodbye."
    }

    return greeting_map.get(qn_simple)


def is_fee_query(q: str) -> bool:
    q = normalize_text(q)
    fee_keys = [
        "fee", "fees", "fee structure", "tuition", "semester fee",
        "first semester fee", "subsequent fee", "total fee", "admission fee",
        "challan", "dues", "charges", "cost", "expense", "expenses",
        "kitni fee", "fees kya hain", "fee kya hai", "fee structure kya hai"
    ]
    return any(k in q for k in fee_keys)


def is_uaf_web_query(q: str) -> bool:
    q = normalize_text(q)
    keys = [
        "uaf", "university", "admission", "admissions", "merit", "notice", "notices",
        "department", "calendar", "hostel", "scholarship", "faculty",
        "teacher", "teachers", "staff", "department faculty", "who teaches",
        "event", "events", "seminar", "workshop", "conference", "webinar",
        "news", "activity", "activities", "campus news", "apply", "prospectus"
    ]
    return any(k in q for k in keys)


def is_live_teacher_names_query(q: str) -> bool:
    q = normalize_text(q)
    teacher_words = [
        "teacher", "teachers", "faculty", "staff", "lecturer",
        "professor", "who teaches", "teacher names", "faculty names",
        "teachers name", "teachers names"
    ]
    dept_words = [
        "department", "bs", "ms", "mphil", "phd",
        "computer science", "cs", "chemistry", "physics",
        "math", "mathematics", "statistics", "botany",
        "zoology", "biochemistry", "software engineering", "it", "agronomy"
    ]
    return any(t in q for t in teacher_words) and any(d in q for d in dept_words)


def is_live_event_query(q: str) -> bool:
    q = normalize_text(q)
    event_words = [
        "event", "events", "seminar", "workshop", "conference",
        "webinar", "latest event", "upcoming event", "activity", "activities"
    ]
    return any(w in q for w in event_words)


def is_notice_query(q: str) -> bool:
    q = normalize_text(q)
    return any(k in q for k in ["notice", "notices", "campus news", "news"])


def is_admission_query(q: str) -> bool:
    q = normalize_text(q)
    return "admission" in q or "admissions" in q or "apply" in q or "prospectus" in q


def is_merit_query(q: str) -> bool:
    q = normalize_text(q)
    return "merit" in q or "merit list" in q or "merit lists" in q or "entry test result" in q or "result" in q


def is_undergrad_query(q: str) -> bool:
    q = normalize_text(q)
    return any(k in q for k in ["undergraduate", "undergrad", "ug", "bsc", "bs", "b.ed", "b.ed."])


def is_postgrad_query(q: str) -> bool:
    q = normalize_text(q)
    return any(k in q for k in ["postgraduate", "postgrad", "pg", "ms", "mphil", "m.phil", "phd", "gre", "gat"])


def is_teacher_query(query: str) -> bool:
    q = normalize_text(query)
    keywords = [
        "dr", "professor", "lecturer", "assistant professor", "associate professor",
        "dean", "vice chancellor", "vc", "chairman", "hod", "head of department",
        "floor", "office", "time", "email", "phone", "contact", "consultation",
        "specialization", "room", "cabin", "extension",
        "ka email", "ki email", "email kya", "office kahan", "office kidhar",
        "floor konsa", "specialization kya", "phone number", "contact number",
        "office time", "timing", "kab milte", "consultation hours"
    ]
    return any(k in q for k in keywords)


def is_simple_general_query(q: str) -> bool:
    q = normalize_text(q)

    simple_starters = [
        "what is", "define", "explain", "difference between",
        "what are", "tell me about", "how does", "what do you mean by"
    ]

    non_simple_markers = [
        "uaf", "faculty", "teacher", "fee", "admission", "notice",
        "hostel", "scholarship", "merit", "event", "seminar", "workshop"
    ]

    if any(m in q for m in non_simple_markers):
        return False

    return any(q.startswith(s) for s in simple_starters) and len(q) < 160


# -------------------------------------------------
# FACULTY / FEE HELPERS
# -------------------------------------------------

def find_faculty(query: str):
    q = normalize_text(query)

    df = get_faculty_df()
    if df.empty:
        return None

    # Exact/contains match on name
    hit = df[df["name_norm"].apply(lambda n: n and n in q)]
    if not hit.empty:
        return hit.iloc[0].to_dict()

    # Match designation too
    if "designation" in df.columns:
        df["designation_norm"] = df["designation"].astype(str).str.lower().str.strip()
        hit = df[df["designation_norm"].apply(lambda d: d and d in q)]
        if not hit.empty:
            return hit.iloc[0].to_dict()

    # Special role aliases
    role_aliases = {
        "vc": ["vice chancellor", "vc"],
        "vice chancellor": ["vice chancellor", "vc"],
        "dean": ["dean"],
        "chairman": ["chairman", "chairperson"],
        "hod": ["hod", "head of department"]
    }

    for _, row in df.iterrows():
        name_val = str(row.get("name", "")).strip().lower()
        designation_val = str(row.get("designation", "")).strip().lower()
        combined = f"{name_val} {designation_val}"

        for role, aliases in role_aliases.items():
            if any(a in q for a in aliases) and role in combined:
                return row.to_dict()

    # Token fallback
    tokens = [t for t in re.split(r"\s+", q) if len(t) >= 4]
    for t in tokens:
        hit = df[df["name_norm"].str.contains(re.escape(t), na=False)]
        if not hit.empty:
            return hit.iloc[0].to_dict()

        if "designation_norm" in df.columns:
            hit = df[df["designation_norm"].str.contains(re.escape(t), na=False)]
            if not hit.empty:
                return hit.iloc[0].to_dict()

    return None


def format_faculty_answer(faculty: dict, user_query: str) -> str:
    q = normalize_text(user_query)

    def val(key):
        v = str(faculty.get(key, "") or "").strip()
        return v if v else "Not available in department database."

    responses = []
    asked_specifics = False

    if "email" in q:
        responses.append(f"Email: {val('email')}")
        asked_specifics = True
    if "floor" in q or "office" in q or "room" in q or "cabin" in q:
        responses.append(f"Office Floor: {val('floor')}")
        asked_specifics = True
    if "time" in q or "hours" in q or "consultation" in q or "timing" in q:
        responses.append(f"Office Hours: {val('office time/student consultation hours')}")
        asked_specifics = True
    if "phone" in q or "contact" in q or "extension" in q or "number" in q:
        responses.append(f"Mobile: {val('contact/phone')}\nOffice Phone: {val('Office Phone')}")
        asked_specifics = True
    if "specialization" in q:
        responses.append(f"Specialization: {val('Specialization')}")
        asked_specifics = True

    if asked_specifics:
        return "\n".join(responses)

    return (
        f"Name: {val('name')}\n"
        f"Designation: {val('designation')}\n"
        f"Floor: {val('floor')}\n"
        f"Office Hours: {val('office time/student consultation hours')}\n"
        f"Email: {val('email')}\n"
        f"Mobile: {val('contact/phone')}\n"
        f"Office Phone: {val('Office Phone')}\n"
        f"Specialization: {val('Specialization')}"
    )


def normalize_fee_query(q: str) -> str:
    q = normalize_text(q)
    q = q.replace("-", " ").replace("_", " ").replace("/", " ")
    q = re.sub(r"\bcs\b", "computer science", q)
    q = re.sub(r"\bbs\s*cs\b", "bscs", q)
    q = re.sub(r"\bbs\s*computer\s*science\b", "bscs", q)
    q = re.sub(r"\b\(?m\)?\b", "morning", q)
    q = re.sub(r"\b\(?e\)?\b", "evening", q)
    q = re.sub(r"\s+", " ", q).strip()
    return q


def fee_search_best(query: str):
    fee_df = get_fee_df_with_shift()
    if fee_df.empty:
        return None

    q = normalize_fee_query(query)

    want_shift = ""
    if "morning" in q or " (m)" in q:
        want_shift = "morning"
    elif "evening" in q or " (e)" in q:
        want_shift = "evening"

    want_level = ""
    if "phd" in q:
        want_level = "phd"
    elif "mphil" in q or "m.phil" in q:
        want_level = "mphil"
    elif "ms" in q or "m.s" in q:
        want_level = "ms"
    elif "bs" in q or "b.s" in q or "bscs" in q:
        want_level = "bs"

    tokens = [t for t in re.findall(r"[a-zA-Z]+", q) if len(t) >= 4]
    if "bscs" in q:
        tokens.extend(["computer", "science"])

    temp = fee_df.copy()

    if want_level:
        temp = temp[
            temp["degree_norm"].str.contains(rf"\b{want_level}\b", na=False) |
            temp["degree_norm"].str.contains(want_level, na=False)
        ]

    if want_shift:
        temp = temp[temp["shift"] == want_shift]

    def score_row(rowtext: str) -> int:
        s = 0
        for t in tokens:
            if t in rowtext:
                s += 2

        if "chemistry" in q and "chemistry" in rowtext:
            s += 10
        if "computer science" in q and "computer science" in rowtext:
            s += 10
        if want_shift and want_shift in rowtext:
            s += 6

        return s

    temp = temp.copy()
    temp["score"] = temp["degree_norm"].apply(score_row)
    temp = temp[temp["score"] > 0].sort_values("score", ascending=False)

    if temp.empty:
        return None

    return temp.iloc[0].to_dict()


def format_fee_answer_one_strict(r: dict, user_query: str) -> str:
    q = normalize_text(user_query)

    prog = str(r.get("degree_program", "")).strip() or "Unknown Program"
    first_fee = str(r.get("first_semester_fee", "")).strip()
    sub_fee = str(r.get("subsequent_fee", "")).strip()
    total_fee = str(r.get("total_fee", "")).strip()

    def safe(v):
        return v if v else "Not available in fee database."

    responses = []
    asked_specifics = False

    if "first" in q or "1st" in q:
        responses.append(f"First Semester Fee: {safe(first_fee)}")
        asked_specifics = True
    if "subsequent" in q or "next" in q:
        responses.append(f"Subsequent Fee: {safe(sub_fee)}")
        asked_specifics = True
    if "total" in q:
        responses.append(f"Total Fee: {safe(total_fee)}")
        asked_specifics = True

    if asked_specifics:
        return f"{prog}\n" + "\n".join(responses)

    return (
        f"{prog}\n"
        f"First Semester Fee: {safe(first_fee)}\n"
        f"Subsequent Fee: {safe(sub_fee)}\n"
        f"Total Fee: {safe(total_fee)}"
    )


# -------------------------------------------------
# STATUS HELPERS
# -------------------------------------------------

def load_users():
    return safe_read_csv(USERS_PATH, columns=["email", "password", "name"])


def load_status():
    if not os.path.exists(STATUS_PATH):
        return {}
    try:
        with open(STATUS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_status(data: dict):
    with open(STATUS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def ensure_status_entries():
    try:
        users = load_users()
        status_data = load_status()
        changed = False

        for _, row in users.iterrows():
            email = str(row.get("email", "")).strip().lower()
            name = str(row.get("name", "")).strip()

            if not email or not name or name.lower() == "unknown":
                continue

            if email not in status_data:
                status_data[email] = {
                    "status": "unknown",
                    "updated_at": ""
                }
                changed = True

        if changed:
            save_status(status_data)
    except Exception:
        pass


# -------------------------------------------------
# LIVE UAF ANSWERING
# -------------------------------------------------

def _format_link_items(title: str, items: list, fallback_url: str):
    if not items:
        return (
            "I could not find relevant information on the UAF official website.",
            "Source: UAF Website (Live)"
        )

    lines = [title + "\n"]
    used = []

    for i, item in enumerate(items[:10], 1):
        line = f"{i}. {item.get('title', 'Untitled')}"
        if item.get("date"):
            line += f" — {item['date']}"
        lines.append(line)

        snippet = str(item.get("snippet", "")).strip()
        if snippet:
            lines.append(f"   {snippet}")

        url = item.get("url", "")
        if url and url not in used:
            used.append(url)

    lines.append("\nSources:")
    for u in used[:10]:
        lines.append(u)

    if not used:
        lines.append(fallback_url)

    return "\n".join(lines).strip(), "Source: UAF Website (Live)"


def answer_department_teachers_from_uaf(user_query: str):
    teachers = live_extract_department_teachers(user_query)

    if not teachers:
        return (
            "I could not find teacher names for that department on the UAF official website.",
            "Source: UAF Website (Live)"
        )

    lines = ["Here are possible teacher/faculty names extracted from the UAF official website:\n"]

    for i, t in enumerate(teachers[:20], 1):
        line = f"{i}. {t.get('name', '')}"
        if t.get("designation"):
            line += f" — {t['designation']}"
        if t.get("department"):
            line += f" ({t['department']})"
        if t.get("email"):
            line += f"\n   Email: {t['email']}"
        if t.get("phone"):
            line += f"\n   Phone: {t['phone']}"
        lines.append(line)

    urls = list(dict.fromkeys([t.get("url", "") for t in teachers if t.get("url")]))
    lines.append("\nSources:")
    if urls:
        for u in urls[:5]:
            lines.append(u)
    else:
        lines.append(FACULTY_DIRECTORY_URL)

    return "\n".join(lines).strip(), "Source: UAF Website (Live)"


def answer_events_from_uaf(user_query: str):
    items = live_extract_uaf_events(user_query)
    return _format_link_items("Here are relevant events from the UAF official website:", items, EVENTS_URL)


def answer_notices_from_uaf(user_query: str):
    items = live_extract_uaf_notices(user_query)
    return _format_link_items("Here are relevant notices/news from the UAF official website:", items, CAMPUS_NEWS_URL)


def answer_admissions_from_uaf(user_query: str):
    if is_postgrad_query(user_query):
        items = live_extract_postgrad_admissions(user_query)
        return _format_link_items("Here is postgraduate admission information from the UAF official website:", items, POSTGRAD_ADM_URL)

    items = live_extract_undergrad_admissions(user_query)
    return _format_link_items("Here is undergraduate admission information from the UAF official website:", items, UNDERGRAD_ADM_URL)


def answer_merit_from_uaf(user_query: str):
    if is_postgrad_query(user_query):
        items = live_extract_postgrad_merit_lists(user_query)
        return _format_link_items("Here are postgraduate merit/result links from the UAF official website:", items, POSTGRAD_ADM_URL)

    items = live_extract_undergrad_merit_lists(user_query)
    return _format_link_items("Here are undergraduate merit/result links from the UAF official website:", items, UNDERGRAD_ADM_URL)


def answer_from_live_uaf(user_query: str):
    sources = live_retrieve_uaf(user_query)

    if not sources:
        return (
            "I couldn’t fetch relevant information from the UAF website right now. Try again or rephrase the question.",
            "Source: UAF Website (Live)"
        )

    context = "\n\n".join(
        [f"[Source {i+1}] {s['url']}\n{s['snippet'][:800]}" for i, s in enumerate(sources)]
    )

    system_prompt = """
You are a UAF academic assistant.
Answer ONLY from the provided live UAF website snippets.
If the answer is not present, say: "Not found on the provided UAF pages."
Keep the answer short and factual.
""".strip()

    try:
        answer = ask_llama(
            system_prompt,
            f"Question: {user_query}\n\nLIVE SOURCES:\n{context}",
            num_predict=OLLAMA_NUM_PREDICT_LIVE
        )
        used_urls = "\n".join([s["url"] for s in sources])
        return (
            f"{answer}\n\nSources:\n{used_urls}",
            "Source: UAF Website (Live)"
        )
    except Exception:
        used_urls = "\n".join([s["url"] for s in sources])
        raw_text = "\n\n".join([s["snippet"] for s in sources])
        return (
            f"{raw_text}\n\nSources:\n{used_urls}",
            "Source: UAF Website (Live)"
        )


# -------------------------------------------------
# ROUTES
# -------------------------------------------------

@app.route("/")
def home():
    return render_template("index.html")


@app.route("/status")
def status_page():
    return render_template("status.html")


@app.route("/api/ask", methods=["POST"])
def ask():
    data = request.get_json() or {}
    user_query = (data.get("query") or "").strip()

    if not user_query:
        return jsonify({"answer": "Please enter a question.", "source": ""})

    instant = get_instant_reply(user_query)
    if instant:
        return jsonify({
            "answer": instant,
            "source": "Source: Instant Response"
        })

    faculty = find_faculty(user_query)

    if is_teacher_query(user_query) and faculty:
        answer = format_faculty_answer(faculty, user_query)
        return jsonify({
            "answer": answer,
            "source": "Source: Faculty CSV (Strict)"
        })

    if is_live_teacher_names_query(user_query):
        answer, source = answer_department_teachers_from_uaf(user_query)
        return jsonify({
            "answer": answer,
            "source": source
        })

    if is_merit_query(user_query):
        answer, source = answer_merit_from_uaf(user_query)
        return jsonify({
            "answer": answer,
            "source": source
        })

    if is_admission_query(user_query):
        answer, source = answer_admissions_from_uaf(user_query)
        return jsonify({
            "answer": answer,
            "source": source
        })

    if is_live_event_query(user_query):
        answer, source = answer_events_from_uaf(user_query)
        return jsonify({
            "answer": answer,
            "source": source
        })

    if is_notice_query(user_query):
        answer, source = answer_notices_from_uaf(user_query)
        return jsonify({
            "answer": answer,
            "source": source
        })

    if is_fee_query(user_query):
        row = fee_search_best(user_query)
        if not row:
            return jsonify({
                "answer": "Not available in fee database (Approved_Fee_2025-26_parsed.csv).",
                "source": "Source: Fee CSV (Strict)"
            })

        return jsonify({
            "answer": format_fee_answer_one_strict(row, user_query),
            "source": "Source: Fee CSV (Strict)"
        })

    if is_uaf_web_query(user_query):
        answer, source = answer_from_live_uaf(user_query)
        return jsonify({
            "answer": answer,
            "source": source
        })

    if not ollama_available():
        return jsonify({
            "answer": "AI model is not available right now. Make sure Ollama is running and the selected model is installed.",
            "source": f"Source: Ollama ({OLLAMA_MODEL})"
        })

    system_prompt = """
You are a helpful Computer Science academic assistant.

When answering:
• Always complete the explanation fully.
• Do NOT stop mid-sentence.
• If explaining a concept, provide a full structured explanation.
• Use bullet points or short sections when useful.

Typical structure:
1. Definition
2. Key Concepts
3. Example (if applicable)

Keep the answer clear and educational, but ensure the explanation is complete.
""".strip()

    try:
        # Optimized for speed
        predict_val = 320 if is_simple_general_query(user_query) else OLLAMA_NUM_PREDICT_GENERAL
        answer = ask_llama(system_prompt, user_query, num_predict=predict_val)

        return jsonify({
            "answer": answer,
            "source": f"Source: Ollama ({OLLAMA_MODEL})"
        })
    except requests.exceptions.Timeout:
        return jsonify({
            "answer": "The AI model is taking too long. Try a shorter question or check your local Ollama status.",
            "source": f"Source: Ollama ({OLLAMA_MODEL}) - Timeout"
        })
    except Exception:
        return jsonify({
            "answer": "The AI model is too slow or unavailable right now. Try again later.",
            "source": f"Source: Ollama ({OLLAMA_MODEL})"
        })


@app.route("/api/ask_stream", methods=["POST"])
def ask_stream():
    data = request.get_json() or {}
    user_query = (data.get("query") or "").strip()
    history = data.get("history") or []

    if not user_query:
        return Response(
            "data: " + json.dumps({"token": "Please enter a question."}) + "\n\n",
            mimetype="text/event-stream"
        )

    instant = get_instant_reply(user_query)
    if instant:
        def instant_stream():
            yield "data: " + json.dumps({
                "token": instant,
                "source": "Source: Instant Response"
            }) + "\n\n"
        return Response(instant_stream(), mimetype="text/event-stream")

    faculty = find_faculty(user_query)

    if is_teacher_query(user_query) and faculty:
        answer = format_faculty_answer(faculty, user_query)

        def one_shot():
            yield "data: " + json.dumps({
                "token": answer,
                "source": "Source: Faculty CSV (Strict)"
            }) + "\n\n"

        return Response(one_shot(), mimetype="text/event-stream")

    if is_live_teacher_names_query(user_query):
        answer, source = answer_department_teachers_from_uaf(user_query)

        def one_shot_live_teachers():
            yield "data: " + json.dumps({
                "token": answer,
                "source": source
            }) + "\n\n"

        return Response(one_shot_live_teachers(), mimetype="text/event-stream")

    if is_merit_query(user_query):
        answer, source = answer_merit_from_uaf(user_query)

        def one_shot_merit():
            yield "data: " + json.dumps({
                "token": answer,
                "source": source
            }) + "\n\n"

        return Response(one_shot_merit(), mimetype="text/event-stream")

    if is_admission_query(user_query):
        answer, source = answer_admissions_from_uaf(user_query)

        def one_shot_adm():
            yield "data: " + json.dumps({
                "token": answer,
                "source": source
            }) + "\n\n"

        return Response(one_shot_adm(), mimetype="text/event-stream")

    if is_live_event_query(user_query):
        answer, source = answer_events_from_uaf(user_query)

        def one_shot_events():
            yield "data: " + json.dumps({
                "token": answer,
                "source": source
            }) + "\n\n"

        return Response(one_shot_events(), mimetype="text/event-stream")

    if is_notice_query(user_query):
        answer, source = answer_notices_from_uaf(user_query)

        def one_shot_notices():
            yield "data: " + json.dumps({
                "token": answer,
                "source": source
            }) + "\n\n"

        return Response(one_shot_notices(), mimetype="text/event-stream")

    if is_fee_query(user_query):
        row = fee_search_best(user_query)
        answer = (
            "Not available in fee database (Approved_Fee_2025-26_parsed.csv)."
            if not row else
            format_fee_answer_one_strict(row, user_query)
        )

        def one_shot_fee():
            yield "data: " + json.dumps({
                "token": answer,
                "source": "Source: Fee CSV (Strict) — Approved_Fee_2025-26_parsed.csv"
            }) + "\n\n"

        return Response(one_shot_fee(), mimetype="text/event-stream")

    if is_uaf_web_query(user_query):
        answer, source = answer_from_live_uaf(user_query)

        def one_shot_live():
            yield "data: " + json.dumps({
                "token": answer,
                "source": source
            }) + "\n\n"

        return Response(one_shot_live(), mimetype="text/event-stream")

    if not ollama_available():
        def no_model():
            yield "data: " + json.dumps({
                "token": "AI model is not available right now. Make sure Ollama is running and the selected model is installed.",
                "source": f"Source: Ollama ({OLLAMA_MODEL})"
            }) + "\n\n"
        return Response(no_model(), mimetype="text/event-stream")

    system_prompt = """
You are a helpful Computer Science academic assistant.

When answering:
• Always complete the explanation fully.
• Do NOT stop mid-sentence.
• If explaining a concept, provide a full structured explanation.
• Use bullet points or short sections when useful.

Typical structure:
1. Definition
2. Key Concepts
3. Example (if applicable)

Keep the answer clear and educational, but ensure the explanation is complete.
""".strip()

    messages = [{"role": "system", "content": system_prompt}]

    for h in history[-3:]:
        role = h.get("role")
        content = h.get("content")
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": str(content)[:220]})

    messages.append({"role": "user", "content": user_query})

    def generate():
        try:
            first = True
            num_predict = 320 if is_simple_general_query(user_query) else OLLAMA_NUM_PREDICT_STREAM

            for token in ollama_stream(messages, num_predict=num_predict):
                payload = {"token": token}
                if first:
                    payload["source"] = f"Source: Ollama ({OLLAMA_MODEL})"
                    first = False
                yield "data: " + json.dumps(payload) + "\n\n"

        except Exception:
            yield "data: " + json.dumps({
                "token": "The AI model is too slow or unavailable right now. Try again later.",
                "source": f"Source: Ollama ({OLLAMA_MODEL})"
            }) + "\n\n"

    return Response(generate(), mimetype="text/event-stream")


@app.route("/api/faculty")
def faculty_api():
    q = (request.args.get("q") or "").strip().lower()
    designation = (request.args.get("designation") or "").strip().lower()

    df = get_faculty_df()
    temp = df.copy()

    if q:
        mask = (
            temp["name"].astype(str).str.lower().str.contains(q, na=False) |
            temp["designation"].astype(str).str.lower().str.contains(q, na=False) |
            temp["Specialization"].astype(str).str.lower().str.contains(q, na=False)
        )
        temp = temp[mask]

    if designation:
        temp = temp[temp["designation"].astype(str).str.lower() == designation]

    items = []
    for _, row in temp.head(200).iterrows():
        items.append({
            "name": row.get("name", ""),
            "designation": row.get("designation", ""),
            "floor": row.get("floor", ""),
            "office_hours": row.get("office time/student consultation hours", ""),
            "email": row.get("email", ""),
            "specialization": row.get("Specialization", "")
        })

    return jsonify({"items": items})


@app.route("/api/status", methods=["GET"])
def get_status():
    ensure_status_entries()

    users = load_users()
    status_data = load_status()

    items = []
    for _, row in users.iterrows():
        email = str(row.get("email", "")).strip().lower()
        name = str(row.get("name", "")).strip()

        if not email or not name or name.lower() == "unknown":
            continue

        s = status_data.get(email, {})
        items.append({
            "name": name,
            "email": email,
            "status": s.get("status", "unknown"),
            "updated_at": s.get("updated_at", "")
        })

    items.sort(key=lambda x: x["name"].lower())
    return jsonify({"items": items})


@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json() or {}
    email = (data.get("email") or "").strip().lower()
    password = (data.get("password") or "").strip()

    if not email or not password:
        return jsonify({"ok": False, "error": "Email and password required"}), 400

    users = load_users()
    hit = users[users["email"].astype(str).str.lower() == email]

    if hit.empty:
        return jsonify({"ok": False, "error": "Invalid credentials"}), 401

    user = hit.iloc[0].to_dict()
    stored_password = str(user.get("password", "")).strip()

    password_ok = False
    try:
        if stored_password.startswith("pbkdf2:") or stored_password.startswith("scrypt:"):
            password_ok = check_password_hash(stored_password, password)
        else:
            password_ok = stored_password == password
    except Exception:
        password_ok = stored_password == password

    if not password_ok:
        return jsonify({"ok": False, "error": "Invalid credentials"}), 401

    session["teacher_email"] = email
    session["teacher_name"] = str(user.get("name", "")).strip()

    return jsonify({"ok": True, "name": session["teacher_name"]})


@app.route("/api/logout", methods=["POST"])
def logout():
    session.pop("teacher_email", None)
    session.pop("teacher_name", None)
    return jsonify({"ok": True})


@app.route("/api/set_status", methods=["POST"])
def set_status():
    teacher_email = session.get("teacher_email")
    if not teacher_email:
        return jsonify({"ok": False, "error": "Not logged in"}), 401

    data = request.get_json() or {}
    status = (data.get("status") or "").strip()

    if status not in ("in_office", "away"):
        return jsonify({"ok": False, "error": "Status must be in_office or away"}), 400

    status_data = load_status()
    status_data[teacher_email] = {
        "status": status,
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    save_status(status_data)

    return jsonify({"ok": True})


@app.route("/api/me", methods=["GET"])
def me():
    email = session.get("teacher_email")
    name = session.get("teacher_name")
    if not email:
        return jsonify({"logged_in": False})

    status_data = load_status()
    s = status_data.get(email, {})
    return jsonify({
        "logged_in": True,
        "email": email,
        "name": name,
        "status": s.get("status", "unknown")
    })

@app.route("/api/title", methods=["POST"])
def generate_title():
    data = request.get_json() or {}
    text = (data.get("text") or "").strip()

    if not text:
        return jsonify({"title": "New Chat"})

    system_prompt = """
Generate a very short chat title (max 6 words).

Rules:
- No punctuation
- No extra explanation
- Only the title
- Must summarize the topic clearly

Examples:
Input: What is machine learning?
Output: Machine Learning Basics

Input: List CS faculty UAF
Output: UAF CS Faculty
""".strip()

    try:
        title = ask_llama(system_prompt, text, num_predict=20)

        # clean title
        title = title.replace("\n", "").strip()
        title = title[:50]

        return jsonify({"title": title})

    except Exception:
        return jsonify({"title": text[:40]})

@app.route("/api/contact", methods=["POST"])
def contact():
    data = request.get_json() or {}
    name = data.get("name", "User").strip()
    user_email = data.get("email", "Not provided").strip()
    msg = data.get("message", "").strip()

    if not msg:
        return jsonify({"error": "Message is required."}), 400

    admin_email = "zohaib190303@gmail.com"

    sender_email = "zohaib190303@gmail.com"
    sender_password = "sfdrojhdabypaxly"

    try:
        message = MIMEMultipart()
        message['From'] = sender_email
        message['To'] = admin_email
        message['Subject'] = f"Smart Dept Assistant: Query from {name}"
        
        body = f"Name: {name}\nEmail: {user_email}\n\nMessage:\n{msg}"
        message.attach(MIMEText(body, 'plain'))
        
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(sender_email, sender_password)
        server.send_message(message)
        server.quit()
        
        return jsonify({"success": True, "message": "Email sent successfully!"})
    except Exception as e:
        print(f"Failed to send email: {e}")
        return jsonify({"error": "Failed to send email due to a server error. Check your credentials and server logs."}), 500

if __name__ == "__main__":
    app.run(debug=True, threaded=True)
