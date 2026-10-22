#!/usr/bin/env python3
"""ZeroCloud Compliance Gateway: step zero before ELM.

Every inbound booking email passes this gate first, on our own hardware:
  1. Fast rules screen every email (milliseconds): control-list HS codes,
     military/dual-use wording, technical specifications, confidential
     contract terms, ID numbers.
  2. A local language model reads only the emails the rules can't decide.
  3. Fail-closed decision: CLEAR goes on to ELM; anything risky or uncertain
     is HELD here until a compliance manager approves or rejects it.
Every step is written to an append-only audit log. Sanctions screening is not
repeated here: TMS already covers it.

Nothing is ever sent to an external AI provider. Standard library only.
"""

import json
import math
import os
import platform
import re
import socket
import threading
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
MODEL = os.environ.get("ZC_MODEL", "llama3.2:3b")
HOST = os.environ.get("ZC_HOST", "0.0.0.0")
PORT = int(os.environ.get("ZC_PORT", "8080"))
DATA_DIR = Path(os.environ.get("ZC_DATA_DIR", ROOT / "data"))

CONTROL = json.loads((ROOT / "rules" / "control_list.json").read_text(encoding="utf-8"))
LIST_VERSION = CONTROL["version"]

# Warm-up and real requests must use identical options, otherwise Ollama
# reloads the model. temperature 0 = same email, same answer. A booking email
# plus the short prompt fits easily in 2048 tokens, and a smaller context is faster.
OPTIONS = {"temperature": 0, "num_ctx": 2048}

# ---------------------------------------------------------------------------
# Stage 1: rules. Run on every email, decide in milliseconds.
# ---------------------------------------------------------------------------

HS_RE = re.compile(r"\b(?:HS|H\.S\.|HS code|tariff code|tariff no\.?|ZTN|CN code)\s*[:#]?\s*"
                   r"(\d{4}(?:[.\s]?\d{2}){0,3})\b", re.I)
GOODS_LINE_RE = re.compile(r"^[ \t]*[-•*][ \t]+\S", re.M)
SPEC_UNITS_RE = re.compile(CONTROL["technical_spec_units_regex"])
PERSONAL_ID_RE = re.compile(CONTROL["personal_id_regex"], re.I)


def word_re(phrase):
    """Match a word or phrase on its own, not inside a longer word."""
    return re.compile(r"(?<![\w-])" + re.escape(phrase) + r"(?![\w-])", re.I)


def line_at(text, pos):
    """The whole line containing position `pos`: shown to the reviewer as evidence."""
    start = text.rfind("\n", 0, pos) + 1
    end = text.find("\n", pos)
    return text[start:len(text) if end == -1 else end].strip()


def normalise(s):
    return re.sub(r"\s+", " ", s or "").strip().lower()


def in_text(quote, text):
    """True if a quote really appears in the email: guards against invented evidence."""
    return bool(normalise(quote)) and normalise(quote) in normalise(text)


def hs_controlled(code):
    return next((e for e in CONTROL["controlled_hs_prefixes"] if code.startswith(e["prefix"])), None)


def screen_rules(text):
    started = time.perf_counter()
    hard, soft = [], []

    def add(bucket, category, label, pos):
        evidence = line_at(text, pos) if pos is not None else ""
        if not any(f["category"] == category and f["label"] == label for f in bucket):
            bucket.append({"category": category, "label": label, "evidence": evidence,
                           "source": "rules", "verified": True})

    hs_codes = []
    for m in HS_RE.finditer(text):
        code = re.sub(r"\D", "", m.group(1))
        hs_codes.append(code)
        entry = hs_controlled(code)
        if entry:
            add(hard, "controlled_goods", f"HS {m.group(1)}: {entry['label']}", m.start())

    for word in CONTROL["military_dual_use_keywords"]:
        m = word_re(word).search(text)
        if m:
            add(hard, "military_dual_use", f"Military or dual-use wording: “{word}”", m.start())

    for word in CONTROL["confidential_markers"]:
        m = word_re(word).search(text)
        if m:
            add(hard, "confidential_contract", f"Confidential contract terms: “{word}”", m.start())

    for m in SPEC_UNITS_RE.finditer(text):
        add(hard, "technical_specification", f"Technical performance value: “{m.group(0)}”", m.start())
    for word in CONTROL["technical_spec_markers"]:
        m = word_re(word).search(text)
        if m:
            add(hard, "technical_specification", f"Technical specification: “{word}”", m.start())

    for m in PERSONAL_ID_RE.finditer(text):
        add(hard, "personal_id", "Passport or ID number (sensitive personal data)", m.start())

    for word in CONTROL["soft_keywords"]:
        m = word_re(word).search(text)
        if m:
            add(soft, "unclear_wording", f"Needs reading in context: “{word}”", m.start())

    goods_without_hs = []
    for m in GOODS_LINE_RE.finditer(text):
        line = line_at(text, m.start())
        if not HS_RE.search(line):
            goods_without_hs.append(line)
            add(soft, "missing_hs_code", f"Goods line without HS code: {line.lstrip('-•* ')}", m.start())
    if not hs_codes:
        add(soft, "missing_hs_code", "No HS codes in the email: goods can't be checked against the lists", None)

    return {"hard": hard, "soft": soft, "hs_codes": hs_codes, "goods_without_hs": goods_without_hs,
            "ms": round((time.perf_counter() - started) * 1000, 1)}


# ---------------------------------------------------------------------------
# Stage 2: the local language model, only for emails the rules can't decide.
# ---------------------------------------------------------------------------

# Speed: on a Pi the model writes only ~5-10 words a second, so the answer form is
# deliberately tiny (four short fields) and capped. Most of the old 150 s was writing.
GATE_CATEGORIES = ["dual_use", "technical_data", "confidential_contract", "personal_data", "none"]

GATE_PROMPT = """You pre-screen freight booking emails before automation.
HOLD if the email contains: goods with possible military use (e.g. drone or UAV parts, aerospace, weapons electronics), technical performance data or drawings, confidential contract terms, or passport/ID numbers.
Otherwise CLEAR. Normal business contacts and packing or fabric details are fine.
Reply briefly: one short reason, and an exact quote of at most 8 words from the email."""

GATE_SCHEMA = {
    "type": "object",
    "properties": {
        "decision": {"type": "string", "enum": ["HOLD", "CLEAR"]},
        "category": {"type": "string", "enum": GATE_CATEGORIES},
        "reason": {"type": "string"},
        "quote": {"type": "string"},
    },
    "required": ["decision", "category", "reason", "quote"],
}
# Ceilings against a runaway answer only: the JSON answers finish well below them, and a
# model that finishes early isn't slowed down. Too tight a ceiling would cut the JSON off.
GATE_MAX_TOKENS = 150

NULLABLE_STR = {"type": ["string", "null"]}

BOOKING_PROMPT = """Extract the key booking details from this freight booking email as JSON.
Copy values exactly as written. Use null when a value is not in the email. Never invent HS codes.
Keep each goods description to at most 6 words."""

BOOKING_SCHEMA = {
    "type": "object",
    "properties": {
        "shipper": NULLABLE_STR,
        "consignee": NULLABLE_STR,
        "origin": NULLABLE_STR,
        "destination": NULLABLE_STR,
        "mode": {"type": "string", "enum": ["sea", "air", "rail", "road", "unknown"]},
        "incoterm": NULLABLE_STR,
        "cargo_ready": NULLABLE_STR,
        "packages": NULLABLE_STR,
        "goods": {"type": "array", "items": {
            "type": "object",
            "properties": {"description": {"type": "string"}, "hs_code": NULLABLE_STR},
            "required": ["description", "hs_code"]}},
    },
    "required": ["shipper", "consignee", "origin", "destination", "mode", "incoterm",
                 "cargo_ready", "packages", "goods"],
}
BOOKING_MAX_TOKENS = 500

# Brevity is guidance, not a limit: the prompt asks for short answers but allows longer
# ones for open-ended questions, and there is no length cap. For speed on a Pi the model
# reads two trimmed excerpts, and the app shows the sources itself so the model needn't.
KB_PROMPT = """You are the on-prem assistant of a compliance manager at a freight forwarder, reviewing a held booking.
You can answer questions, draft emails or messages to the customer, summarise and rephrase. This is routine internal compliance work.
Base facts and policy on the knowledge-base excerpts. For drafts, write freely using the case details.
Emails to customers ask for standard compliance documents, such as an end-use statement, export licence status or HS codes;
they never ask anyone to disclose confidential contract terms.
Be succinct: usually one or two sentences for answers, a short complete text for drafts. Skip preambles."""
KB_MAX_TOKENS = None  # no cap
KB_SECTIONS = 2
KB_EXCERPT_WORDS = 100

# ---------------------------------------------------------------------------
# Knowledge base: Markdown files in knowledge/, searched with a small BM25
# keyword ranking (standard library only, no embedding model to download).
# ---------------------------------------------------------------------------

STOPWORDS = set("""the and for with this that are was were from what which can should does how why our
your not may into about there their them they has have had will would could its than then when where
who whom also any all but only such these those under over more most other some been being""".split())


def terms(text):
    return [w for w in re.findall(r"[a-zäöüß0-9]+", text.lower()) if len(w) > 2 and w not in STOPWORDS]


def load_knowledge():
    chunks = []
    for path in sorted((ROOT / "knowledge").glob("*.md")):
        text = path.read_text(encoding="utf-8")
        title = re.search(r"^# (.+)$", text, re.M)
        title = title.group(1).strip() if title else path.stem
        for part in re.split(r"^## ", text, flags=re.M)[1:]:
            heading, _, body = part.partition("\n")
            chunks.append({"doc": title, "section": heading.strip(), "text": body.strip(),
                           "terms": Counter(terms(f"{title} {heading} {body}"))})
    doc_freq = Counter(t for c in chunks for t in set(c["terms"]))
    idf = {t: math.log(1 + (len(chunks) - d + 0.5) / (d + 0.5)) for t, d in doc_freq.items()}
    avg_len = sum(sum(c["terms"].values()) for c in chunks) / max(len(chunks), 1)
    return chunks, idf, avg_len


KB, KB_IDF, KB_AVG_LEN = load_knowledge()


def bm25(chunk, query):
    length = sum(chunk["terms"].values())
    score = 0.0
    for t in set(terms(query)):
        tf = chunk["terms"][t]
        if tf:
            score += KB_IDF[t] * tf * 2.2 / (tf + 1.2 * (0.25 + 0.75 * length / KB_AVG_LEN))
    return score


def search_kb(query, k=3, context="", context_weight=0.3):
    """The k sections that best match the query (BM25 scoring). The optional context,
    such as the case's hold reasons, only breaks close calls between sections."""
    scored = []
    for chunk in KB:
        main = bm25(chunk, query)
        if main > 0:
            scored.append((main + context_weight * bm25(chunk, context), chunk))
    scored.sort(key=lambda pair: -pair[0])
    return [chunk for _, chunk in scored[:k]]


def ollama_request(path, payload=None, timeout=300):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(f"{OLLAMA_URL}{path}", data=data,
                                 headers={"Content-Type": "application/json"})
    return urllib.request.urlopen(req, timeout=timeout)


def chat_payload(system, user, schema, **extra):
    payload = {
        "model": MODEL,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "keep_alive": -1,  # keep the model in RAM forever: no reload mid-demo
        "options": OPTIONS,
        **extra,
    }
    if schema:
        payload["format"] = schema  # Ollama constrains the answer to this JSON schema
    return payload


def describe_error(exc):
    if isinstance(exc, urllib.error.HTTPError):
        detail = exc.read().decode(errors="replace")
        try:
            detail = json.loads(detail).get("error", detail)
        except ValueError:
            pass
        hint = f" Run on the Pi: ollama pull {MODEL}" if "not found" in detail else ""
        return f"Ollama error: {detail}.{hint}"
    if isinstance(exc, (urllib.error.URLError, OSError)):
        return f"Can't reach Ollama at {OLLAMA_URL} ({exc}). Check on the Pi: sudo systemctl status ollama"
    return f"Local AI error: {exc}"


def run_model(system, user, schema, max_tokens, emit, stage="gate"):
    """Stream one short answer from the local model: parsed JSON when a schema is given,
    plain text otherwise. Raises on any failure, including JSON cut off by the length cap."""
    started = time.time()
    content, final = "", {}
    payload = chat_payload(system, user, schema, stream=True)
    if max_tokens:  # a ceiling against runaway JSON only; free-text answers run uncapped
        payload["options"] = {**OPTIONS, "num_predict": max_tokens}
    with ollama_request("/api/chat", payload) as resp:
        for line in resp:
            if not line.strip():
                continue
            chunk = json.loads(line)
            if "error" in chunk:
                raise RuntimeError(chunk["error"])
            piece = chunk.get("message", {}).get("content", "")
            if piece:
                content += piece
                emit({"type": "token", "stage": stage, "text": piece})
            if chunk.get("done"):
                final = chunk
    data = json.loads(content) if schema else content.strip()
    eval_count = final.get("eval_count") or 0
    eval_seconds = (final.get("eval_duration") or 0) / 1e9
    return data, {"model": MODEL, "seconds": round(time.time() - started, 1), "output_tokens": eval_count,
                  "tokens_per_second": round(eval_count / eval_seconds, 1) if eval_seconds else None}


def warm_up():
    """Load the model into RAM at startup and pre-process the gate prompt."""
    for _ in range(60):
        try:
            payload = chat_payload(GATE_PROMPT, "Warm-up.", GATE_SCHEMA, stream=False)
            payload["options"] = {**OPTIONS, "num_predict": 1}
            with ollama_request("/api/chat", payload) as resp:
                resp.read()
            print(f"[zerocloud] model {MODEL} loaded and warm", flush=True)
            return
        except (urllib.error.URLError, OSError) as exc:
            print(f"[zerocloud] waiting for Ollama ({exc})", flush=True)
            time.sleep(5)


def ai_findings(text, rules, ai):
    """Turn the model's short answer into HOLD reasons. Fail-closed: any doubt is a reason."""
    reasons = []
    quote = ai.get("quote") or ""
    if ai.get("decision") != "CLEAR":
        reasons.append({"category": ai.get("category") or "unclear", "label": ai.get("reason") or "Local AI recommends review",
                        "evidence": quote, "source": "local AI", "verified": in_text(quote, text)})
    # The model can't clear what the lists can't check: goods without HS codes stay held.
    for f in rules["soft"]:
        if f["category"] == "missing_hs_code":
            reasons.append({**f, "label": f"{f['label']} (fail-closed)"})
    return reasons


# ---------------------------------------------------------------------------
# Storage: cases, append-only audit log and the simulated ELM hand-off.
# Everything stays on this device.
# ---------------------------------------------------------------------------

LOCK = threading.Lock()
CASES_FILE = DATA_DIR / "cases.json"
AUDIT_FILE = DATA_DIR / "audit.jsonl"
ELM_FILE = DATA_DIR / "elm_outbox.jsonl"


def now():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def load_cases():
    try:
        return json.loads(CASES_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def save_cases(cases):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CASES_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(cases, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(CASES_FILE)


def append_jsonl(path, record):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def case_history(case_id):
    """Every audit-log entry for one case, oldest first."""
    try:
        lines = AUDIT_FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    entries = (json.loads(line) for line in lines if line.strip())
    return [e for e in entries if e.get("case") == case_id]


def audit(event, case_id, actor, details):
    append_jsonl(AUDIT_FILE, {"time": now(), "case": case_id, "event": event, "actor": actor,
                              "details": details, "list_version": LIST_VERSION})


def parse_email(raw):
    headers, body, in_headers = {}, [], True
    for line in raw.splitlines():
        if in_headers:
            m = re.match(r"^(From|To|Subject|Date):\s*(.*)$", line, re.I)
            if m:
                headers[m.group(1).lower()] = m.group(2).strip()
                continue
            in_headers = False
            if not line.strip():
                continue
        body.append(line)
    return headers, "\n".join(body)


def send_to_elm(case, released_by):
    """Hand the email to ELM. Cleared or approved cases follow the normal flow in full."""
    record = {"time": now(), "case": case["id"], "gateway": case["status"], "released_by": released_by,
              "subject": case["subject"], "from": case["from"], "email": case["raw"],
              "booking": case.get("booking"), "list_version": LIST_VERSION}
    append_jsonl(ELM_FILE, record)
    return record


def screen_email(raw, emit):
    headers, _ = parse_email(raw)
    rules = screen_rules(raw)
    emit({"type": "rules", "rules": rules})

    ai, ai_stats, ai_error = None, None, None
    if rules["hard"]:
        decision, decided_by, reasons = "HOLD", "rules", list(rules["hard"])
    elif not rules["soft"]:
        decision, decided_by, reasons = "CLEAR", "rules", []
    else:
        decided_by = "rules + local AI"
        emit({"type": "ai_start", "model": MODEL})
        try:
            ai, ai_stats = run_model(GATE_PROMPT, raw, GATE_SCHEMA, GATE_MAX_TOKENS, emit)
            reasons = ai_findings(raw, rules, ai)
        except (BrokenPipeError, ConnectionResetError):
            raise  # the browser went away: stop, don't record a decision
        except Exception as exc:  # any model failure holds the email (fail-closed)
            ai_error = describe_error(exc)
            reasons = [{"category": "ai_error", "label": f"Local AI unavailable, held (fail-closed): {ai_error}",
                        "evidence": "", "source": "gateway", "verified": True}]
        emit({"type": "ai_done", "ai": ai, "stats": ai_stats, "error": ai_error})
        decision = "HOLD" if reasons else "CLEAR"

    with LOCK:
        cases = load_cases()
        case = {
            "id": f"ZC-{1001 + len(cases)}",
            "received": now(),
            "from": headers.get("from", "unknown sender"),
            "subject": headers.get("subject", "(no subject)"),
            "raw": raw,
            "decision": decision,
            "decided_by": decided_by,
            "status": "released_to_elm" if decision == "CLEAR" else "held",
            "findings": rules["hard"] + rules["soft"],
            "reasons": reasons,
            "rules_ms": rules["ms"],
            "ai": ai,
            "ai_stats": ai_stats,
            "review": None,
        }
        cases.append(case)
        save_cases(cases)
        audit("screened", case["id"], "gateway",
              f"{decision} by {decided_by}; {len(reasons)} reason(s); rules {rules['ms']} ms")
        if decision == "CLEAR":
            send_to_elm(case, "gateway")
            audit("released_to_elm", case["id"], "gateway", "Cleared automatically, no compliance findings")
    emit({"type": "case", "case": case})
    extract_booking(case, emit)


def extract_booking(case, emit):
    """After the decision: the local AI turns the email into structured booking data.
    Cleared bookings hand it to ELM; held ones keep it on this box for the reviewer."""
    raw = case["raw"]
    emit({"type": "extract_start"})
    try:
        booking, stats = run_model(BOOKING_PROMPT, raw, BOOKING_SCHEMA, BOOKING_MAX_TOKENS, emit, stage="extract")
    except (BrokenPipeError, ConnectionResetError):
        raise
    except Exception as exc:
        emit({"type": "booking", "error": describe_error(exc)})
        return
    digits = re.sub(r"\D", "", raw)
    unverified = [g["hs_code"] for g in booking.get("goods") or []
                  if g.get("hs_code") and re.sub(r"\D", "", g["hs_code"]) not in digits]
    update_case(case["id"], booking=booking, booking_unverified=unverified)
    audit("booking_extracted", case["id"], "local AI", f"Structured booking data extracted on-prem with {MODEL}")
    to_elm = case["decision"] == "CLEAR"
    if to_elm:
        append_jsonl(ELM_FILE, {"time": now(), "case": case["id"], "type": "structured_booking", "booking": booking})
    emit({"type": "booking", "booking": booking, "unverified": unverified, "stats": stats, "to_elm": to_elm})


def update_case(case_id, **fields):
    with LOCK:
        cases = load_cases()
        for c in cases:
            if c["id"] == case_id:
                c.update(fields)
        save_cases(cases)


def ask_kb(case_id, question, reviewer, emit):
    """Answer a reviewer's question from the local knowledge base (retrieval + local AI)."""
    case = next((c for c in load_cases() if c["id"] == case_id), None)
    if not case:
        emit({"type": "error", "text": "Case not found."})
        return
    # One reason per kind keeps the context short (and doesn't repeat "confidential" five times).
    by_kind = {}
    for r in case["reasons"]:
        by_kind.setdefault(r["category"], r["label"])
    reasons = "; ".join(by_kind.values()) or "no findings"
    # The last two exchanges, so follow-ups like "draft an email asking them to do this" make sense.
    earlier = case.get("qa", [])[-2:]
    history = "\n".join(f"Q: {qa['question']}\nA: {' '.join(qa['answer'].split()[:80])}" for qa in earlier)

    # The question leads; hold reasons and the last answer only break close calls.
    hits = search_kb(question, KB_SECTIONS, context=f"{reasons} {history}") or search_kb(reasons, KB_SECTIONS)
    sources = [{"doc": h["doc"], "section": h["section"]} for h in hits]
    emit({"type": "sources", "sources": sources})
    excerpts = "\n\n".join(f"{h['section']}: {' '.join(h['text'].split()[:KB_EXCERPT_WORDS])}" for h in hits)

    # A compact case summary instead of the whole email: enough to draft from, quick to read.
    headers, body = parse_email(case["raw"])
    goods = [line_at(body, m.start()) for m in GOODS_LINE_RE.finditer(body)]
    case_summary = (f"From: {headers.get('from', 'unknown')}\nSubject: {case['subject']}\n"
                    f"Goods:\n" + "\n".join(goods or ["(no goods lines)"]) + f"\nHeld because: {reasons}")
    user = (f"Knowledge-base excerpts:\n{excerpts or '(none matched)'}\n\nCase {case['id']}:\n{case_summary}\n\n"
            + (f"Earlier in this review:\n{history}\n\n" if history else "")
            + f"Request: {question}")
    try:
        answer, stats = run_model(KB_PROMPT, user, None, KB_MAX_TOKENS, emit, stage="kb")
    except (BrokenPipeError, ConnectionResetError):
        raise
    except Exception as exc:
        emit({"type": "error", "text": describe_error(exc)})
        return
    qa = {"question": question, "answer": answer, "sources": sources, "time": now(), "stats": stats}
    with LOCK:
        cases = load_cases()
        for c in cases:
            if c["id"] == case_id:
                c.setdefault("qa", []).append(qa)
        save_cases(cases)
        audit("kb_query", case_id, reviewer, f"Asked the knowledge base: {question[:100]}")
    emit({"type": "answer", "qa": qa})


def decide(case_id, body):
    action = body.get("action")
    reviewer = (body.get("reviewer") or "").strip()
    comment = (body.get("comment") or "").strip()
    if action not in ("approve", "reject"):
        return 400, {"error": "action must be approve or reject"}
    if not reviewer:
        return 400, {"error": "Enter the reviewer's name for the audit log."}
    if action == "reject" and not comment:
        return 400, {"error": "Enter a rejection reason for the audit log."}
    with LOCK:
        cases = load_cases()
        case = next((c for c in cases if c["id"] == case_id), None)
        if not case:
            return 404, {"error": "Case not found."}
        if case["status"] != "held":
            return 409, {"error": f"Case already {case['status'].replace('_', ' ')}."}
        case["review"] = {"reviewer": reviewer, "comment": comment, "action": action, "time": now()}
        if action == "approve":
            case["status"] = "approved"
            send_to_elm(case, reviewer)
            audit("approved", case_id, reviewer, f"Released to ELM. Comment: {comment}" if comment else "Released to ELM.")
        else:
            case["status"] = "rejected"
            audit("rejected", case_id, reviewer, f"Not released to ELM. Reason: {comment}")
        save_cases(cases)
    return 200, {"case": case}


def reset_demo():
    with LOCK:
        for path in (CASES_FILE, ELM_FILE):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        audit("demo_reset", "-", "presenter", "Cases and ELM hand-off cleared; audit log kept")


# ---------------------------------------------------------------------------
# Device status: shows the audience where the AI runs.
# ---------------------------------------------------------------------------

_internet_cache = {"at": 0.0, "online": False}


def internet_reachable():
    """True if any public DNS server answers. Cached for 5 s."""
    if time.time() - _internet_cache["at"] > 5:
        online = False
        for host in ("1.1.1.1", "8.8.8.8"):
            try:
                socket.create_connection((host, 53), timeout=1.5).close()
                online = True
                break
            except OSError:
                pass
        _internet_cache.update(at=time.time(), online=online)
    return _internet_cache["online"]


def device_status():
    status = {"device": platform.node(), "cpu_temp_c": None, "mem_used_gb": None, "mem_total_gb": None}
    try:
        status["device"] = Path("/proc/device-tree/model").read_text().strip("\x00 \n")
    except OSError:
        pass
    try:
        status["cpu_temp_c"] = round(int(Path("/sys/class/thermal/thermal_zone0/temp").read_text()) / 1000, 1)
    except (OSError, ValueError):
        pass
    try:
        info = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines())
        total = int(info["MemTotal"].split()[0]) / 1024 ** 2
        available = int(info["MemAvailable"].split()[0]) / 1024 ** 2
        status["mem_total_gb"] = round(total, 1)
        status["mem_used_gb"] = round(total - available, 1)
    except (OSError, KeyError, ValueError):
        pass
    return status


def model_loaded():
    try:
        with ollama_request("/api/ps", timeout=2) as resp:
            models = json.load(resp).get("models", [])
        return any(m.get("name") == MODEL or m.get("model") == MODEL for m in models)
    except (urllib.error.URLError, OSError, ValueError):
        return None  # Ollama not reachable


def load_samples():
    samples = []
    for path in sorted((ROOT / "samples").glob("*.txt")):
        title = re.sub(r"^\d+-", "", path.stem).replace("-", " ").capitalize()
        samples.append({"title": title, "text": path.read_text(encoding="utf-8")})
    return samples


# ---------------------------------------------------------------------------
# Web server.
# ---------------------------------------------------------------------------

CASE_PATH = re.compile(r"^/api/cases/(ZC-\d+)/(decision|ask)$")
HISTORY_PATH = re.compile(r"^/api/cases/(ZC-\d+)/history$")


class Handler(BaseHTTPRequestHandler):
    def send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        try:
            return json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return {}

    def start_stream(self):
        """One JSON object per line, so the browser can show progress live."""
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

        def emit(event):
            self.wfile.write((json.dumps(event, ensure_ascii=False) + "\n").encode())
            self.wfile.flush()
        return emit

    def do_GET(self):
        self.path = urlsplit(self.path).path  # ignore ?query parts, e.g. cache-busting
        if self.path == "/":
            body = (ROOT / "static" / "index.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/api/status":
            held = sum(1 for c in load_cases() if c["status"] == "held")
            self.send_json({**device_status(), "internet": internet_reachable(), "model": MODEL,
                            "model_loaded": model_loaded(), "list_version": LIST_VERSION, "held": held})
        elif self.path == "/api/samples":
            self.send_json(load_samples())
        elif self.path == "/api/cases":
            self.send_json(list(reversed(load_cases())))
        elif (m := HISTORY_PATH.match(self.path)):
            self.send_json(case_history(m.group(1)))
        elif self.path == "/api/rules":
            self.send_json({"version": LIST_VERSION, "disclaimer": CONTROL["disclaimer"]})
        elif self.path == "/favicon.ico":
            self.send_response(204)  # no icon; keeps the browser console clean
            self.end_headers()
        else:
            self.send_json({"error": "not found"}, 404)

    def do_POST(self):
        self.path = urlsplit(self.path).path
        try:
            if self.path == "/api/screen":
                text = (self.read_body().get("text") or "").strip()
                if not text:
                    self.send_json({"error": "Paste an email first."}, 400)
                    return
                screen_email(text, self.start_stream())
            elif self.path == "/api/reset":
                reset_demo()
                self.send_json({"ok": True})
            elif (m := CASE_PATH.match(self.path)):
                case_id, action = m.groups()
                body = self.read_body()
                if action == "decision":
                    status, payload = decide(case_id, body)
                    self.send_json(payload, status)
                else:
                    question = (body.get("question") or "").strip()
                    if not question:
                        self.send_json({"error": "Type a question first."}, 400)
                        return
                    reviewer = (body.get("reviewer") or "").strip() or "compliance reviewer"
                    ask_kb(case_id, question, reviewer, self.start_stream())
            else:
                self.send_json({"error": "not found"}, 404)
        except (BrokenPipeError, ConnectionResetError):
            pass  # the browser tab was closed mid-answer

    def log_message(self, fmt, *args):
        if "/api/status" not in self.path:  # the UI polls status every few seconds
            super().log_message(fmt, *args)


if __name__ == "__main__":
    threading.Thread(target=warm_up, daemon=True).start()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"[zerocloud] gateway on http://{HOST}:{PORT} with {MODEL} via {OLLAMA_URL}, list {LIST_VERSION}",
          flush=True)
    server.serve_forever()
