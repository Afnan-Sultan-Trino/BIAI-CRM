"""Exact text work that code does better (and cheaper) than AI."""
import re

_TABLE = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")
_BD_MOBILE = re.compile(r"^01[3-9]\d{8}$")

# Words that appear in Banglish (Bangla written in English letters) but not in English.
_BANGLISH = {
    "ami", "apni", "apnader", "amar", "amader", "koto", "kibhabe", "kothay", "hobe",
    "vai", "bhai", "naki", "gula", "korbo", "korle", "pabo", "bolen", "jay", "hoy",
    "pawa", "mane", "achhe", "ache", "kemon", "dhonnobad", "lagbe", "chai", "kobe",
    "apnar", "achi", "pathabo", "pathate", "jogajog", "shigroi", "janaben", "janan", "dekhchi",
    "korben", "dhonnobad", "bujhte", "jante", "amader",
}
_SPAM_WORDS = ("crypto", "signals", "bit.ly", "tinyurl", "click now", "earn ")


def bn_to_en_digits(s):
    """০১৭১১ -> 01711"""
    return (s or "").translate(_TABLE)


def normalize_phone(raw):
    """Return a Bangladeshi mobile as 01XXXXXXXXX, or None if it is not valid."""
    if not raw:
        return None
    d = re.sub(r"\D", "", bn_to_en_digits(str(raw)))
    if d.startswith("0088"):
        d = d[2:]
    if d.startswith("880"):
        d = d[2:]
    if len(d) == 10 and d.startswith("1"):
        d = "0" + d
    return d if _BD_MOBILE.match(d) else None


def find_phones_in_text(text):
    """Every valid BD mobile number found inside a message (Bangla digits too)."""
    t = bn_to_en_digits(text or "")
    found = []
    for m in re.finditer(r"\+?\d+(?:[\s\-]\d+)*", t):
        groups = re.findall(r"\d+", m.group())
        for i in range(len(groups)):
            acc = ""
            for g in groups[i:]:
                acc += g
                if len(acc) > 14:
                    break
                p = normalize_phone(acc)
                if p:
                    if p not in found:
                        found.append(p)
                    break
    return found


def split_name(raw):
    """'Mahmud Hasan (Managing Director, Gazipur textile company)' -> name/role/company."""
    raw = (raw or "").strip()
    m = re.match(r"^(?P<name>[^()]+?)\s*\((?P<extra>[^)]*)\)\s*$", raw)
    if not m:
        return {"name": raw, "role": "", "company": ""}
    parts = [p.strip() for p in m.group("extra").split(",", 1)]
    return {
        "name": m.group("name").strip(),
        "role": parts[0],
        "company": parts[1] if len(parts) > 1 else "",
    }


def detect_language(text):
    """bangla (Bangla script) | banglish | english. Used to check the AI, and as a fallback."""
    t = text or ""
    bn = sum(1 for c in t if "\u0980" <= c <= "\u09FF")
    letters = sum(1 for c in t if c.isalpha())
    if letters and bn / letters > 0.3:
        return "bangla"
    words = set(re.findall(r"[a-z]+", t.lower()))
    return "banglish" if words & _BANGLISH else "english"


def extract_headcount(text):
    """'40-person', '120 employees', '১৫ জন' -> number of people, else None."""
    t = bn_to_en_digits(text or "")
    m = re.search(r"(\d{1,4})\s*[- ]?\s*(person|people|employees?|staff|members?|participants?|জন)", t, re.I)
    return int(m.group(1)) if m else None


def spam_signals(text):
    """Cheap hints only. The AI makes the final call."""
    t = (text or "").lower()
    return [w for w in _SPAM_WORDS if w in t]
