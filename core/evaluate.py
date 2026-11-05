"""Score AI output against the hand-made answer key."""
import re

from . import clean

PRICE = re.compile(r"(\d[\d,.]*\s*(tk|taka|bdt|usd|৳|টাকা))|(৳\s*\d)|%", re.I)
_TITLES = re.compile(r"^(prof|dr|md|mr|mrs|ms|engr)\.?\s+", re.I)
CHECKS = ("kind", "want", "lang", "phone", "name", "reply_lang", "reply_safe", "reply_present")


def expected_phone(row):
    return clean.normalize_phone(row.get("phone")) or (clean.find_phones_in_text(row.get("message", "")) or [None])[0]


def score(row, a, key):
    sender = clean.split_name(row.get("sender name") or "")["name"]
    name = (a.get("name") or "").strip()
    reply = (a.get("reply") or "").strip()
    spam = key["kind"] == "spam"
    plain = lambda n: _TITLES.sub("", n.strip()).casefold()      # noqa: E731
    name_ok = (spam and not name) or (bool(name) and (
        plain(name) == plain(sender) or name in row.get("message", "")))
    return {
        "kind": a.get("kind") == key["kind"] or a.get("kind") in key.get("accept", []),
        "want": a.get("want") == key["want"],
        "lang": a.get("lang") == key["lang"],
        "phone": clean.normalize_phone(a.get("phone")) == expected_phone(row),
        "name": name_ok,
        "reply_lang": (not reply) or clean.detect_language(reply) == key["lang"],
        "reply_safe": not PRICE.search(reply),
        "reply_present": (not reply) if spam else bool(reply),
    }


def totals(all_scores):
    n = len(all_scores)
    return {c: sum(1 for s in all_scores if s[c]) for c in CHECKS} | {"n": n}


def add_context(rows):
    """Tell each message how many EARLIER messages came from the same phone (Rafi asked twice)."""
    seen = {}
    for r in rows:
        p = expected_phone(r)
        r["earlier_messages"] = seen.get(p, 0) if p else 0
        if p:
            seen[p] = seen.get(p, 0) + 1
    return rows
