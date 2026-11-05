"""Business rules in plain code: same input, same answer, every time."""
from collections import Counter, defaultdict
from datetime import datetime

from .schema import (DEMO_NOW, FOLLOWUP_HOURS, FOLLOWUP_STATUSES,
                     HIGH_VALUE_HEADCOUNT, PIPELINE_KINDS)


def parse_ts(s):
    return datetime.fromisoformat(str(s).strip().replace(" ", "T"))


def demo_now():
    return parse_ts(DEMO_NOW)


def route(kind):
    """Which screen does this enquiry belong on? Unanalysed ones wait in the pipeline."""
    return "pipeline" if kind is None or kind in PIPELINE_KINDS else "triage"


def is_high_value(want, headcount):
    return want == "corporate" or (headcount or 0) >= HIGH_VALUE_HEADCOUNT


def priority(kind, want, headcount):
    if kind == "urgent":
        return "critical"
    if kind == "support":
        return "high"
    if kind == "lead":
        return "high" if is_high_value(want, headcount) else "medium"
    return "low"


def waiting_since(card):
    """The clock starts at the first message, and resets only when staff contact them.
    A customer sending a second message does NOT reset it (that means we ignored them)."""
    return card.get("last_contact_at") or card["first_message_at"]


def hours_waiting(card, now):
    return (now - parse_ts(waiting_since(card))).total_seconds() / 3600


def is_overdue(card, now, hours=FOLLOWUP_HOURS):
    return (card.get("queue") == "pipeline"
            and card.get("status") in FOLLOWUP_STATUSES
            and hours_waiting(card, now) > hours)


def find_duplicate_groups(rows, phone_key="phone"):
    """Group row ids that share a (normalised) phone. Returns {phone: [ids]} for groups > 1."""
    g = defaultdict(list)
    for r in rows:
        if r.get(phone_key):
            g[r[phone_key]].append(r["id"])
    return {p: ids for p, ids in g.items() if len(ids) > 1}


def summarize(cards, now):
    """All the numbers for the weekly summary. The AI only writes sentences around these."""
    return {
        "total_cards": len(cards),
        "not_analysed": sum(1 for c in cards if not c.get("kind")),
        "by_status": dict(Counter(c["status"] for c in cards if c["queue"] == "pipeline")),
        "by_kind": dict(Counter(c["kind"] for c in cards if c.get("kind"))),
        "by_want": dict(Counter(c["want"] for c in cards if c.get("want") and c["queue"] == "pipeline")),
        "by_channel": dict(Counter(c["channel"] for c in cards)),
        "overdue": [c["id"] for c in cards if is_overdue(c, now)],
        "urgent": [c["id"] for c in cards if c.get("kind") == "urgent"],
        "high_value": [c["id"] for c in cards if c["queue"] == "pipeline"
                       and c.get("kind") == "lead" and is_high_value(c.get("want"), c.get("headcount"))],
        "repeat_askers": [c["id"] for c in cards if c.get("message_count", 1) > 1],
    }
