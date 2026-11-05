"""Weekly summary: code owns the numbers, AI only writes the sentences, code then checks the numbers."""
import json
import re
import time

from . import ai, clean, rules

SYSTEM = """You write the weekly summary for the sales team of BIAI, a Bangladeshi AI training company.
Plain English, under 120 words, no headings. Use ONLY the numbers and names in the JSON: never calculate, round or invent a figure. Mention urgent customers and anyone waiting 48 hours or more, by name. Finish with a line "Next actions:" followed by exactly 3 short actions."""


def facts_from(cards, now):
    """Everything the summary may talk about, computed in code."""
    stats = rules.summarize(cards, now)
    names = {c["id"]: c["name"] or "Unknown" for c in cards}
    pipeline = [c for c in cards if c["queue"] == "pipeline"]
    return {
        "total_enquiries": len(cards),
        "in_pipeline": len(pipeline),
        "pipeline_by_status": stats["by_status"],
        "pipeline_by_programme": stats["by_want"],
        "enquiries_by_channel": stats["by_channel"],
        "not_sales": {k: v for k, v in stats["by_kind"].items() if k not in ("lead", "faq")},
        "waiting_48h_or_more": [names[i] for i in stats["overdue"]],
        "urgent_customers": [names[i] for i in stats["urgent"]],
        "high_value_leads": [f'{c["name"]} ({c["want"]}' + (f', {c["headcount"]} people' if c.get("headcount") else "") + ")"
                             for c in cards if c["id"] in stats["high_value"]],
        "asked_more_than_once": [names[i] for i in stats["repeat_askers"]],
    }


def numbers_in(text):
    return {int(n) for n in re.findall(r"\d+", clean.bn_to_en_digits(text or ""))}


def unverified_numbers(text, facts):
    """Numbers in the AI's text that are not in the facts we gave it (hint: it invented or miscounted)."""
    allowed = numbers_in(json.dumps(facts, ensure_ascii=False)) | {1, 2, 3, 48}
    return sorted(n for n in numbers_in(text) if n not in allowed)


def fallback_summary(f):
    w = len(f["waiting_48h_or_more"])
    parts = [f'{f["total_enquiries"]} enquiries came in; {f["in_pipeline"]} are real sales prospects in the pipeline.']
    if f["not_sales"]:
        parts.append("Not sales: " + ", ".join(f"{v} {k}" for k, v in f["not_sales"].items()) + ".")
    if f["urgent_customers"]:
        parts.append("Urgent: " + ", ".join(f["urgent_customers"]) + ".")
    parts.append(f"{w} prospect(s) have waited 48 hours or more" + (": " + ", ".join(f["waiting_48h_or_more"]) + "." if w else "."))
    return " ".join(parts) + "\n\nNext actions:\n1. Call the urgent customers first.\n2. Reply to everyone waiting 48h+.\n3. Send quotes to the high-value corporate leads."


def write_summary(facts, order=("groq", "gemini"), clients=None, sleep=time.sleep):
    """-> (text, meta, unverified_numbers). Falls back to a plain code-written summary if no AI answers."""
    errors = []
    for name in order:
        prov = ai.get_provider(name)
        client = (clients or {}).get(name)
        if client is None and ai.has_key(prov):
            client = ai.make_client(prov)
        if client is None:
            errors.append(f"{name}: no key")
            continue
        try:
            text = ai._complete(client, prov, json.dumps(facts, ensure_ascii=False), SYSTEM, sleep, json_mode=False).strip()
            if text:
                return text, {"provider": name, "model": prov.model, "source": "live"}, unverified_numbers(text, facts)
        except Exception as e:
            errors.append(f"{name}: {type(e).__name__}")
    return fallback_summary(facts), {"provider": "offline", "source": "offline", "errors": errors}, []
