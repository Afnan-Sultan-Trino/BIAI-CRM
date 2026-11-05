"""AI step: one message in, one JSON analysis out. Groq or Gemini (both OpenAI-compatible).

Design rules:
- one message per call (small prompts stay inside free-tier token limits)
- temperature 0, JSON only, every field validated afterwards in code (db.apply_analysis)
- cache first, live AI second, offline guess last, so the demo never dies on a rate limit
"""
import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

from . import clean

ROOT = Path(__file__).resolve().parents[1]
CACHE_PATH = ROOT / "data" / "analysis_cache.json"
PROMPT_VERSION = "v4"      # bump when the prompt changes: old cached answers are then ignored


class QuotaExhausted(Exception):
    """Daily free quota is gone. Retrying will not help."""


# ---------------------------------------------------------------- settings
def _secrets():
    p = ROOT / ".streamlit" / "secrets.toml"
    if not p.exists():
        return {}
    text = p.read_text(encoding="utf-8-sig")          # utf-8-sig: Windows Notepad adds a BOM
    try:
        import tomllib
        return tomllib.loads(text)
    except Exception:
        return dict(re.findall(r'^\s*([A-Za-z0-9_]+)\s*=\s*"([^"]*)"', text, re.M))


def get_setting(name, default=None):
    v = os.environ.get(name) or _secrets().get(name) or default
    if isinstance(v, str):
        v = v.strip()          # a trailing space or newline in a key breaks the HTTP header
    return v


@dataclass(frozen=True)
class Provider:
    name: str
    base_url: str
    model: str
    key_env: str
    gap_seconds: float      # pause between calls when running many (rate limits)


def get_provider(name):
    if name == "groq":
        return Provider("groq", "https://api.groq.com/openai/v1",
                        get_setting("GROQ_MODEL", "openai/gpt-oss-120b"), "GROQ_API_KEY", 6.0)
    if name == "gemini":
        return Provider("gemini", "https://generativelanguage.googleapis.com/v1beta/openai/",
                        get_setting("GEMINI_MODEL", "gemini-2.5-flash"), "GEMINI_API_KEY", 4.0)
    raise ValueError(f"unknown provider {name}")


def has_key(provider):
    return bool(get_setting(provider.key_env))


def make_client(provider):
    from openai import OpenAI
    key = get_setting(provider.key_env)
    if os.name == "nt" or get_setting("TLS_WORKAROUND") == "1":
        # Workaround that made Groq reachable on this Windows machine (proxy/TLS quirks).
        # It turns certificate checking OFF, so it is used on Windows only. The deployed app
        # (Linux) takes the normal, verified path below.
        import httpx
        http_client = httpx.Client(trust_env=False, verify=False, http2=False,
                                   timeout=httpx.Timeout(60.0, connect=15.0))
        return OpenAI(api_key=key, base_url=provider.base_url, timeout=60, max_retries=0,
                      http_client=http_client)
    return OpenAI(api_key=key, base_url=provider.base_url, timeout=60, max_retries=0)


# ---------------------------------------------------------------- prompt
SYSTEM_PROMPT = """You are the intake assistant for BIAI, a Bangladeshi AI training company. Products: bootcamp (3-day), fellowship, corporate training (for companies).
You receive ONE enquiry as JSON. Return ONE JSON object only (no prose, no code fences) with exactly these keys:
{"kind","want","lang","name","company","role","phone","headcount","summary","reply"}

KIND definitions (choose the BEST fit):
- lead    = a real prospect who wants to join or buy, or asks for a quote or a call.
- faq     = a general or pre-sale question with NO buying signal.
- support = an existing student with a problem (paid but no confirmation).
- urgent  = an angry customer, cancellation/refund demand, or a threat to go public.
- spam    = ads, scams, promo links, anything selling something unrelated.
- job     = someone looking for a job at BIAI.
- minor   = a school student (under 18, e.g. "class 9").

LEAD vs FAQ boundary:
- Choose "lead" if the person shows personal buying intent, even while asking a question.
  Buying signals: "would this suit me?", "can I join?", "how do I pay?", "can I pay in parts?",
  "I have X years of experience", "for my team", "for my company", asking for a quote or a callback,
  asking whether the programme fits THEIR background.
- Choose "faq" only for a general question about how things work, with NO personal buying signal.
- Rule of thumb: "Can I ... ?" about themselves joining / paying / fitting -> lead.
  "Do you ... ?" / "What is ... ?" about how things work -> faq.
  Examples:
    LEAD: "I am a school teacher with 5 years of experience. Would the bootcamp suit me?"
    LEAD: "Can I pay half now and half later?"
    FAQ : "Do I need to bring my own laptop?"

WANT:
- "corporate" if they mention a company, organisation, team, employees, staff, faculty, or ask for a group quote.
- "bootcamp" / "fellowship" if they name the product.
- "unknown" if the message doesn't say. A free masterclass = unknown.

LANG (detect how they wrote, not what country they are from):
- "bangla"   = uses Bangla script
- "banglish" = Bangla words in Latin letters (vai, koto, korbo, kibhabe, apnader)
- "english"  = English words, no Bangla and no Banglish markers

NAME rules:
- Fill "name" from sender_name when it looks like a person's name, e.g. "Rafi Ahmed" -> "Rafi Ahmed".
- Move job titles and company descriptions OUT of the name into "role" and "company", e.g.
  "Jane Doe (Director, a mill in Savar)" -> name "Jane Doe", role "Director", company "a mill in Savar".
- Leave "name" EMPTY only when sender_name is a company or spam handle, or there is no sender_name and no name in the message.

PHONE: only digits that appear in phone_field or in the message, in English digits. NEVER invent a number. If none, null.
HEADCOUNT: how many people are to be trained, if stated, else null ("40-person", "120 employees", "15 jon" -> 40 / 120 / 15).
SUMMARY: one short English sentence for staff: what they want and the next action.

REPLY: a short, warm WhatsApp-style reply in the SAME language and script as the message (english -> English; banglish -> casual Bangla in English letters; bangla -> Bangla script). Short, simple sentences. Use the first name; in a Bangla-script reply write the name in Bangla script too.
  YOU KNOW NOTHING about BIAI's programmes except their three names. NEVER state, confirm, deny or hint at ANY fact about them: price, dates, schedule, format (live, recorded, online, offline), recordings, location, certificates, job outcomes, who a programme suits, discounts, instalments, refunds, or what a term or add-on means. Never write "we can", "we offer", "we provide" or "it is designed for". Do not answer the question itself: say the team will confirm it.
  Never promise WHEN anyone will reply or call (no "soon", "shortly", "this week"). The only exception is an urgent case: "a manager will call you today".
  Never promise a quote, proposal, discount or call, and never agree to a time they suggest. Say the team will review their request and get back to them; if they ask for a call at a certain time, say the team will try to arrange it and confirm.
  Ask at most ONE short question, and only if it helps: team size or preferred dates ONLY for corporate (company or team) enquiries; for an individual who has not named a programme, ask which programme interests them; for an individual who already named the programme, ask NO question. Vary your wording and do not end every reply with the same question.
  If earlier_messages_from_this_person is above 0, they wrote before and got no answer: apologise briefly for the delay.
  If channel is "Phone call note", the person is not in the chat: write a short follow-up message staff can send them.
  By kind:
    spam    -> reply MUST be ""
    urgent  -> apologise sincerely, say a manager will call today, promise nothing else
    support -> apologise, ask for payment method and transaction ID, say the team will check
    job     -> thank them and say you will pass their message to the right person. Do NOT say what this channel is for
    minor   -> be kind, ask them to talk to a parent or guardian before joining, say the team will confirm what is available
"""


def build_user_message(row):
    return json.dumps({
        "channel": row.get("channel"),
        "sender_name": row.get("sender name") or row.get("sender_name") or "",
        "phone_field": row.get("phone") or "",
        "message": row.get("message") or "",
        "earlier_messages_from_this_person": row.get("earlier_messages") or 0,
    }, ensure_ascii=False)


# ---------------------------------------------------------------- parsing
def extract_json(text):
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.I).strip()
    try:
        obj = json.loads(t)
    except Exception:
        s, e = t.find("{"), t.rfind("}")
        if s == -1 or e <= s:
            raise ValueError("no JSON object in model output")
        obj = json.loads(t[s:e + 1])
    if isinstance(obj, list) and obj and isinstance(obj[0], dict):
        obj = obj[0]
    if not isinstance(obj, dict):
        raise ValueError("model output is not a JSON object")
    return obj


def coerce(a):
    """Tidy types. Enum checking and the 'no invented phone' rule happen in db.apply_analysis."""
    def s(x):
        if not isinstance(x, str):
            return ""
        for bad, good in (("\u2011", "-"), ("\u2010", "-"), ("\u202f", " "), ("\u00a0", " "), ("\u200b", "")):
            x = x.replace(bad, good)        # models sometimes emit non-breaking hyphens/spaces
        return x.strip()
    hc = a.get("headcount")
    if isinstance(hc, str) and hc.strip().isdigit():
        hc = int(hc.strip())
    return {
        "kind": s(a.get("kind")).lower(), "want": s(a.get("want")).lower(),
        "lang": s(a.get("lang") or a.get("language")).lower(),
        "name": s(a.get("name")), "company": s(a.get("company")), "role": s(a.get("role")),
        "phone": clean.normalize_phone(a.get("phone")),
        "headcount": hc if isinstance(hc, int) and not isinstance(hc, bool) else None,
        "summary": s(a.get("summary")), "reply": s(a.get("reply")),
    }


# ---------------------------------------------------------------- calling
def _is_daily(e):
    return bool(re.search(r"per day|daily|PerDay|RPD", str(e), re.I))


def _is_retryable(e):
    return (getattr(e, "status_code", None) in (429, 500, 502, 503, 504)
            or e.__class__.__name__ in ("APIConnectionError", "APITimeoutError"))


def _retry_after(e):
    try:
        return float(e.response.headers.get("retry-after"))
    except Exception:
        return None


def call_with_retry(fn, tries=4, sleep=time.sleep):
    for i in range(tries):
        try:
            return fn()
        except Exception as e:
            if getattr(e, "status_code", None) == 429 and _is_daily(e):
                raise QuotaExhausted(str(e)) from e
            if i == tries - 1 or not _is_retryable(e):
                raise
            sleep(min(_retry_after(e) or 2 * (2 ** i), 60))


def _chat(client, provider, user, json_mode, system=None):
    kw = dict(model=provider.model, temperature=0, max_tokens=1500,
              messages=[{"role": "system", "content": system or SYSTEM_PROMPT}, {"role": "user", "content": user}])
    if json_mode:
        kw["response_format"] = {"type": "json_object"}
    return client.chat.completions.create(**kw).choices[0].message.content


def _complete(client, provider, user, system, sleep, json_mode=True):
    def go(jm):
        return call_with_retry(lambda: _chat(client, provider, user, jm, system), sleep=sleep)
    if not json_mode:
        return go(False)
    try:
        return go(True)
    except Exception as e:
        if getattr(e, "status_code", None) == 400:      # provider rejected JSON mode -> plain mode
            return go(False)
        raise


def analyse_message(row, provider, client, sleep=time.sleep):
    """Live AI call for one message -> (analysis dict, meta)."""
    t0 = time.time()
    a = coerce(extract_json(_complete(client, provider, build_user_message(row), SYSTEM_PROMPT, sleep)))
    return a, {"provider": provider.name, "model": provider.model, "source": "live",
               "seconds": round(time.time() - t0, 2)}


# ---------------------------------------------------------------- offline fallback and cache
_REPLY_FALLBACK = {
    "english": "Thank you for contacting BIAI. Our team will get back to you shortly.",
    "banglish": "Apnar message-ta peyechi, dhonnobad. Amader team shigroi apnar sathe jogajog korbe.",
    "bangla": "আপনার মেসেজের জন্য ধন্যবাদ। আমাদের টিম শীঘ্রই আপনার সাথে যোগাযোগ করবে।",
}


def fallback_analysis(row):
    """Used only when no AI is reachable. Deliberately simple and labelled as a guess."""
    msg = row.get("message") or ""
    sender = clean.split_name(row.get("sender name") or row.get("sender_name") or "")
    low = msg.lower()
    lang = clean.detect_language(msg)
    spam = bool(clean.spam_signals(msg))
    want = ("fellowship" if "fellow" in low or "ফেলো" in msg else "bootcamp" if "bootcamp" in low
            else "corporate" if clean.extract_headcount(msg) else "unknown")
    return {"kind": "spam" if spam else "lead", "want": want, "lang": lang,
            "name": sender["name"], "company": sender["company"], "role": sender["role"],
            "phone": clean.normalize_phone(row.get("phone")) or (clean.find_phones_in_text(msg) or [None])[0],
            "headcount": clean.extract_headcount(msg),
            "summary": "Offline guess (AI unavailable): please review.",
            "reply": "" if spam else _REPLY_FALLBACK[lang]}


def cache_key(row):
    raw = (f'{PROMPT_VERSION}|{row.get("sender name") or row.get("sender_name") or ""}|'
           f'{row.get("message") or ""}|{row.get("earlier_messages") or 0}')
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def load_cache(path=CACHE_PATH):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_cache(cache, path=CACHE_PATH):
    Path(path).write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")


def analyse(row, order=("groq", "gemini"), cache=None, clients=None, use_cache=True, sleep=time.sleep):
    """cache -> live providers in order -> offline guess. Always returns (analysis, meta)."""
    cache = {} if cache is None else cache
    key = cache_key(row)
    if use_cache and key in cache:
        return coerce(cache[key]["analysis"]), {**cache[key]["meta"], "source": "cache"}
    errors = []
    for name in order:
        prov = get_provider(name)
        client = (clients or {}).get(name)
        if client is None and has_key(prov):
            client = make_client(prov)
        if client is None:
            errors.append(f"{name}: no key")
            continue
        try:
            a, meta = analyse_message(row, prov, client, sleep=sleep)
            cache[key] = {"analysis": a, "meta": meta}
            return a, meta
        except Exception as e:                                   # try the next provider
            errors.append(f"{name}: {type(e).__name__}: {str(e)[:120]}")
    return fallback_analysis(row), {"provider": "offline", "model": "-", "source": "offline",
                                    "seconds": 0, "errors": errors}


# ---------------------------------------------------------------- second AI pass: fact-check the reply
JUDGE_PROMPT = """You fact-check a draft customer reply for BIAI, an AI training company in Bangladesh. The assistant was given NO facts about BIAI except three product names: bootcamp, fellowship, corporate training.
A CLAIM is a statement that tells the customer something factual about BIAI's programmes, policies or offers: prices, dates, schedule, format (live, recorded, online, offline), recordings, location, certificates, job outcomes, eligibility or who a programme suits, discounts, instalments, refunds, what a term means, or an offer such as "we can arrange".
These are NOT claims, never list them: "the team will confirm / check / follow up / call you / send details", "a manager will call you today", apologies, thanks, greetings, questions to the customer, and repeating what the customer said.
Examples. Claims: "Classes are live", "We can arrange instalments", "The fellowship suits professionals". Not claims: "Our team will confirm the price", "A manager will call you today", "Which batch do you prefer?"
Return JSON only: {"claims": ["short description", ...]}  (empty list if none)."""


def judge_reply(row, reply, provider, client, sleep=time.sleep):
    """Returns a list of unsupported claims found in the reply (empty list = clean)."""
    if not (reply or "").strip():
        return []
    user = json.dumps({"customer_message": row.get("message") or "", "draft_reply": reply}, ensure_ascii=False)
    out = extract_json(_complete(client, provider, user, JUDGE_PROMPT, sleep))
    claims = out.get("claims")
    return [str(c) for c in claims] if isinstance(claims, list) else []
