"""SQLite storage. One card per person (matched by phone), many messages per card."""
import json
import sqlite3

from .clean import extract_headcount, find_phones_in_text, normalize_phone, split_name
from .rules import priority, route
from .schema import KINDS, LANGS, STATUSES, WANTS

SCHEMA = """
CREATE TABLE IF NOT EXISTS enquiries(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT, company TEXT, role TEXT, phone TEXT, channel TEXT,
  want TEXT, kind TEXT, language TEXT, priority TEXT,
  status TEXT NOT NULL DEFAULT 'New', queue TEXT NOT NULL DEFAULT 'pipeline',
  headcount INTEGER, summary TEXT, reply TEXT,
  first_message_at TEXT NOT NULL, last_contact_at TEXT, ai_json TEXT);
CREATE TABLE IF NOT EXISTS messages(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  enquiry_id INTEGER NOT NULL REFERENCES enquiries(id),
  src_id TEXT, channel TEXT, received_at TEXT, sender_raw TEXT, text TEXT);
CREATE INDEX IF NOT EXISTS idx_phone ON enquiries(phone);
"""


def connect(path=":memory:"):
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def ingest_row(conn, row):
    """Add one raw message. Same phone as an existing card -> attach, don't duplicate.
    Returns (enquiry_id, is_new_card)."""
    text = row["message"]
    ts = row.get("received at") or row.get("received_at")
    sender = row.get("sender name") or row.get("sender_name") or ""
    phone = normalize_phone(row.get("phone"))
    if not phone:
        found = find_phones_in_text(text)
        phone = found[0] if found else None
    eid, is_new = None, False
    if phone:
        r = conn.execute("SELECT id FROM enquiries WHERE phone=?", (phone,)).fetchone()
        eid = r["id"] if r else None
    if eid is None:
        p = split_name(sender)
        cur = conn.execute(
            "INSERT INTO enquiries(name,company,role,phone,channel,headcount,first_message_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (p["name"], p["company"], p["role"], phone, row.get("channel"),
             extract_headcount(text), ts))
        eid, is_new = cur.lastrowid, True
    conn.execute(
        "INSERT INTO messages(enquiry_id,src_id,channel,received_at,sender_raw,text) VALUES (?,?,?,?,?,?)",
        (eid, str(row.get("id", "")), row.get("channel"), ts, sender, text))
    conn.commit()
    return eid, is_new


def _text_has_phone(phone, texts):
    return any(phone in find_phones_in_text(t) for t in texts)


def apply_analysis(conn, eid, a):
    """Store the AI result, but only values we can trust. Bad values are dropped, not guessed."""
    msgs = [r["text"] for r in conn.execute("SELECT text FROM messages WHERE enquiry_id=?", (eid,))]
    card = dict(conn.execute("SELECT * FROM enquiries WHERE id=?", (eid,)).fetchone())
    kind = a.get("kind") if a.get("kind") in KINDS else None
    want = a.get("want") if a.get("want") in WANTS else "unknown"
    lang = a.get("lang") or a.get("language")
    lang = lang if lang in LANGS else None
    name = (a.get("name") or "").strip() or card["name"]
    phone = card["phone"]
    ai_phone = normalize_phone(a.get("phone"))
    if not phone and ai_phone and _text_has_phone(ai_phone, msgs):   # never accept an invented number
        phone = ai_phone
    headcount = a.get("headcount") if isinstance(a.get("headcount"), int) else card["headcount"]
    status = "Lost" if kind == "spam" else card["status"]
    conn.execute(
        "UPDATE enquiries SET name=?,company=?,role=?,phone=?,want=?,kind=?,language=?,"
        "headcount=?,summary=?,reply=?,queue=?,priority=?,status=?,ai_json=? WHERE id=?",
        (name, (a.get("company") or card["company"] or ""), (a.get("role") or card["role"] or ""),
         phone, want, kind, lang, headcount, a.get("summary") or a.get("note") or "",
         a.get("reply") or "", route(kind), priority(kind, want, headcount), status,
         json.dumps(a, ensure_ascii=False), eid))
    conn.commit()


def set_status(conn, eid, status, now):
    """Staff moved the card. Anything past 'New' counts as contact, which resets the 48h clock."""
    if status not in STATUSES:
        raise ValueError(f"bad status: {status}")
    last = None if status == "New" else str(now)
    conn.execute("UPDATE enquiries SET status=?, last_contact_at=? WHERE id=?", (status, last, eid))
    conn.commit()


def get_cards(conn):
    rows = conn.execute(
        "SELECT e.*, (SELECT COUNT(*) FROM messages m WHERE m.enquiry_id=e.id) AS message_count,"
        " (SELECT group_concat(src_id) FROM messages m WHERE m.enquiry_id=e.id) AS src_ids"
        " FROM enquiries e ORDER BY e.id").fetchall()
    return [dict(r) for r in rows]


def get_messages(conn, eid):
    return [dict(r) for r in conn.execute(
        "SELECT * FROM messages WHERE enquiry_id=? ORDER BY received_at", (eid,))]


def update_latest(conn, eid, a):
    """A newer message from someone we already have: refresh summary and reply, keep the rest of the card."""
    conn.execute("UPDATE enquiries SET summary=?, reply=? WHERE id=?",
                 (a.get("summary") or a.get("note") or "", a.get("reply") or "", eid))
    conn.commit()


def count_messages_by_phone(conn, phone):
    if not phone:
        return 0
    return conn.execute("SELECT COUNT(*) FROM messages m JOIN enquiries e ON e.id=m.enquiry_id WHERE e.phone=?",
                        (phone,)).fetchone()[0]


def next_src_id(conn):
    """Sample rows are 1..20; pasted enquiries continue 21, 22, ... (never reuse a number)."""
    return str((conn.execute("SELECT MAX(CAST(src_id AS INTEGER)) FROM messages").fetchone()[0] or 0) + 1)
