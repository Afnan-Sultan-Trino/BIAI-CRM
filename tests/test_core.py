import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core import clean, db, rules  # noqa: E402

KEY = json.loads((ROOT / "tests" / "answer_key.json").read_text(encoding="utf-8"))
KEY = {int(k): v for k, v in KEY.items() if not k.startswith("_")}
ROWS = list(csv.DictReader(open(ROOT / "data" / "sample-enquiries.csv", encoding="utf-8-sig")))


def loaded(with_analysis=False):
    conn = db.connect()
    for r in ROWS:
        db.ingest_row(conn, r)
    if with_analysis:
        for c in db.get_cards(conn):
            first_src = int(c["src_ids"].split(",")[0])
            k = KEY[first_src]
            db.apply_analysis(conn, c["id"], {"kind": k["kind"], "want": k["want"], "lang": k["lang"]})
    return conn


# ---------- clean.py ----------
def test_bangla_digits():
    assert clean.bn_to_en_digits("০১৮৯৮৭৬৫৪৩২") == "01898765432"


def test_phone_formats():
    for raw in ["01711000001", "+8801711000001", "8801711000001", "0088 01711000001",
                "017-1100-0001", "01711 000001", "০১৭১১০০০০০১", "1711000001"]:
        assert clean.normalize_phone(raw) == "01711000001", raw
    for bad in ["", None, "12345", "01111000001", "abc"]:
        assert clean.normalize_phone(bad) is None, bad


def test_phone_in_text():
    assert clean.find_phones_in_text("amar nam rafiq, number 01712345678") == ["01712345678"]
    assert clean.find_phones_in_text("আমার নম্বর ০১৮৯৮৭৬৫৪৩২।") == ["01898765432"]
    assert clean.find_phones_in_text("call 01711000001 25 tarikh") == ["01711000001"]
    assert clean.find_phones_in_text("Earn 5000 USD every day, 2026-10-28 09:12") == []


def test_split_name():
    a = clean.split_name("Mahmud Hasan (Managing Director, Gazipur textile company)")
    assert a == {"name": "Mahmud Hasan", "role": "Managing Director", "company": "Gazipur textile company"}
    b = clean.split_name("Kamrul Islam (HR, a bank in Motijheel)")
    assert (b["name"], b["role"], b["company"]) == ("Kamrul Islam", "HR", "a bank in Motijheel")
    c = clean.split_name("Rezaul Karim (owner, small garment factory)")
    assert (c["name"], c["role"], c["company"]) == ("Rezaul Karim", "owner", "small garment factory")
    assert clean.split_name("Sabbir")["name"] == "Sabbir"


def test_language_detector_matches_key():
    wrong = [r["id"] for r in ROWS if clean.detect_language(r["message"]) != KEY[int(r["id"])]["lang"]]
    assert wrong == [], f"language detector disagrees on {wrong}"


def test_headcount():
    by_id = {int(r["id"]): clean.extract_headcount(r["message"]) for r in ROWS}
    assert by_id[3] == 40 and by_id[10] == 120 and by_id[18] == 15
    assert [i for i, v in by_id.items() if v is not None and i not in (3, 10, 18)] == []


def test_spam_hint_only_on_spam():
    flagged = [int(r["id"]) for r in ROWS if clean.spam_signals(r["message"])]
    assert flagged == [11]


# ---------- db.py: ingest and duplicates ----------
def test_dataset_shape():
    assert len(ROWS) == 20


def test_ingest_merges_rafi():
    conn = loaded()
    cards = db.get_cards(conn)
    assert len(cards) == 19
    rafi = next(c for c in cards if c["phone"] == "01711000001")
    assert rafi["message_count"] == 2 and rafi["src_ids"] == "1,12"
    assert rules.find_duplicate_groups(
        [{"id": int(r["id"]), "phone": clean.normalize_phone(r["phone"])} for r in ROWS]) == {"01711000001": [1, 12]}


def test_missing_phones_stay_empty():
    conn = loaded()
    missing = sorted(int(c["src_ids"].split(",")[0]) for c in db.get_cards(conn) if not c["phone"])
    assert missing == [2, 6, 9, 11, 13, 17]


def test_company_and_role_split_on_ingest():
    conn = loaded()
    c = next(c for c in db.get_cards(conn) if c["phone"] == "01711000003")
    assert (c["name"], c["role"], c["company"], c["headcount"]) == (
        "Mahmud Hasan", "Managing Director", "Gazipur textile company", 40)


# ---------- rules.py: routing and the 48h clock ----------
def test_routing_with_answer_key():
    conn = loaded(with_analysis=True)
    cards = db.get_cards(conn)
    pipeline = sorted(int(c["src_ids"].split(",")[0]) for c in cards if c["queue"] == "pipeline")
    triage = sorted(int(c["src_ids"].split(",")[0]) for c in cards if c["queue"] == "triage")
    assert triage == [7, 11, 13, 16, 20]
    assert pipeline == [1, 2, 3, 4, 5, 6, 8, 9, 10, 14, 15, 17, 18, 19]
    spam = next(c for c in cards if c["kind"] == "spam")
    assert spam["status"] == "Lost"
    urgent = next(c for c in cards if c["kind"] == "urgent")
    assert urgent["priority"] == "critical"


def test_high_value_flags():
    conn = loaded(with_analysis=True)
    s = rules.summarize(db.get_cards(conn), rules.demo_now())
    ids = {c["id"]: int(c["src_ids"].split(",")[0]) for c in db.get_cards(conn)}
    assert sorted(ids[i] for i in s["high_value"]) == [3, 10, 18]
    assert sorted(ids[i] for i in s["urgent"]) == [16]


def test_overdue_clock():
    conn = loaded(with_analysis=True)
    now = rules.demo_now()
    cards = db.get_cards(conn)
    first = {c["id"]: int(c["src_ids"].split(",")[0]) for c in cards}
    overdue = sorted(first[c["id"]] for c in cards if rules.is_overdue(c, now))
    assert overdue == [1, 2, 3, 4, 5, 6, 8, 9, 10]      # 26 Oct messages, minus support/spam


def test_rafis_second_message_does_not_reset_clock():
    conn = loaded(with_analysis=True)
    rafi = next(c for c in db.get_cards(conn) if c["phone"] == "01711000001")
    assert rules.waiting_since(rafi) == "2026-10-26 09:12"
    assert rules.is_overdue(rafi, rules.demo_now())


def test_contacting_resets_clock():
    conn = loaded(with_analysis=True)
    now = rules.demo_now()
    rafi = next(c for c in db.get_cards(conn) if c["phone"] == "01711000001")
    db.set_status(conn, rafi["id"], "Contacted", now)
    rafi = next(c for c in db.get_cards(conn) if c["phone"] == "01711000001")
    assert not rules.is_overdue(rafi, now)


# ---------- db.py: never trust invented values ----------
def test_ai_cannot_invent_phone():
    conn = loaded()
    tahmina = next(c for c in db.get_cards(conn) if c["name"] == "Tahmina Akter")
    db.apply_analysis(conn, tahmina["id"], {"kind": "lead", "want": "fellowship", "lang": "bangla",
                                            "phone": "01799999999"})
    assert next(c for c in db.get_cards(conn) if c["id"] == tahmina["id"])["phone"] is None


def test_ai_phone_accepted_when_in_message():
    conn = db.connect()
    eid, _ = db.ingest_row(conn, {"id": "99", "channel": "WhatsApp", "received_at": "2026-10-28 10:00",
                                  "sender_name": "Sadia", "phone": "",
                                  "message": "আমার নাম সাদিয়া। আমার নম্বর ০১৮৯৮৭৬৫৪৩২।"})
    db.apply_analysis(conn, eid, {"kind": "lead", "want": "fellowship", "lang": "bangla", "phone": "01898765432"})
    assert db.get_cards(conn)[0]["phone"] == "01898765432"


def test_bad_enum_values_dropped():
    conn = loaded()
    c = db.get_cards(conn)[0]
    db.apply_analysis(conn, c["id"], {"kind": "VIP!!", "want": "everything", "lang": "klingon"})
    c = db.get_cards(conn)[0]
    assert c["kind"] is None and c["want"] == "unknown" and c["language"] is None
