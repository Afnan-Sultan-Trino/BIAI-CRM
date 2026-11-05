"""Runs the real Streamlit app headlessly (AppTest) with a fake AI, and clicks through it."""
import csv
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from streamlit.testing.v1 import AppTest  # noqa: E402

from core import ai, db, evaluate, rules, summary  # noqa: E402

KEY = {int(k): v for k, v in json.loads((ROOT / "tests" / "answer_key.json").read_text(encoding="utf-8")).items()
       if not k.startswith("_")}
ROWS = evaluate.add_context(list(csv.DictReader(open(ROOT / "data" / "sample-enquiries.csv", encoding="utf-8-sig"))))
NEW_MSG = "Hi, this is Tania from a pharma company in Tejgaon. We need AI training for 60 of our staff. Please call me on +880 1911-000999."


def reply_for(row):
    k = KEY[int(row["id"])]
    if k["kind"] == "spam":
        return ""
    return ("Sorry for the delay, Rafi. Our team will confirm." if row.get("earlier_messages")
            else {"bangla": "ধন্যবাদ, টিম নিশ্চিত করবে।", "banglish": "Dhonnobad, team confirm korbe.",
                  "english": "Thanks, our team will confirm."}[k["lang"]])


def handler(system, user):
    if system == ai.SYSTEM_PROMPT:
        u = json.loads(user)
        row = next((r for r in ROWS if r["message"] == u["message"]), None)
        if row is None:                                               # a brand-new pasted message
            return json.dumps({"kind": "lead", "want": "corporate", "lang": "english", "name": "Tania",
                               "company": "a pharma company in Tejgaon", "role": "", "phone": "01911000999",
                               "headcount": 60, "summary": "Corporate training for 60 staff; call back.",
                               "reply": "Hi Tania, our team will confirm the details. Which programme interests you?"})
        k = KEY[int(row["id"])]
        return json.dumps({"kind": k["kind"], "want": k["want"], "lang": k["lang"],
                           "name": ai.clean.split_name(row["sender name"])["name"],
                           "company": ai.clean.split_name(row["sender name"])["company"],
                           "role": ai.clean.split_name(row["sender name"])["role"],
                           "phone": evaluate.expected_phone(row), "headcount": ai.clean.extract_headcount(row["message"]),
                           "summary": f"{k['kind']} enquiry", "reply": reply_for(row)})
    if system == summary.SYSTEM:
        return "We received 19 enquiries and 57 customers were lost.\n\nNext actions:\n1. a\n2. b\n3. c"
    raise AssertionError("unexpected prompt")


def fake_client():
    def create(**kw):
        content = handler(kw["messages"][0]["content"], kw["messages"][1]["content"])
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(ai, "has_key", lambda p: False)               # tests never touch the network
    cache, client = {}, fake_client()
    for r in ROWS:
        ai.analyse(r, order=("groq",), cache=cache, clients={"groq": client})
    path = tmp_path / "cache.json"
    ai.save_cache(cache, path)
    monkeypatch.setattr(ai, "CACHE_PATH", path)
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60)
    at.session_state["ai_clients"] = {"groq": client}
    return at.run()


def go(at, screen):
    return at.sidebar.radio(key="nav").set_value(screen).run()


def click(at, label_part):
    b = next(b for b in at.button if label_part in b.label)
    return b.click().run()


def cards(at):
    return db.get_cards(at.session_state["conn"])


def test_pipeline_loads_and_counts_are_right(app):
    assert not app.exception
    assert [m.value for m in app.metric] == ["19", "14", "9", "3", "1"]
    texts = [m.value for m in app.markdown]
    assert any("New (14)" in t for t in texts)
    assert any("asked 2×" in t for t in texts)                         # Rafi, merged
    assert any("waiting 2d" in t for t in texts)


def test_rafi_reply_is_the_apology(app):
    app.button(key="open_p_1").click().run()
    assert any("Sorry for the delay" in c.value for c in app.code)


def test_open_and_mark_contacted_resets_the_48h_clock(app):
    app.button(key="open_p_1").click().run()
    app.button(key="contacted_1").click().run()
    assert next(c for c in cards(app) if c["id"] == 1)["status"] == "Contacted"
    assert [m.value for m in app.metric][2] == "8"                    # one fewer overdue
    assert any("Contacted (1)" in m.value for m in app.markdown)


def test_triage_screen_separates_non_sales(app):
    go(app, "Triage")
    assert not app.exception
    labels = [e.label for e in app.expander]
    assert "Urgent (1)" in labels and "Support (1)" in labels and "Spam (1)" in labels and "Job seekers (1)" in labels
    assert any("Habibur" in m.value for m in app.markdown)


def test_add_enquiry_end_to_end(app):
    go(app, "Add enquiry")
    app.text_area(key="add_msg").set_value(NEW_MSG).run()
    click(app, "Analyse with AI")
    assert app.session_state["draft"]["a"]["headcount"] == 60
    assert any("Hi Tania" in c.value for c in app.code)
    click(app, "Save to CRM")
    assert len(cards(app)) == 20
    assert app.sidebar.radio(key="nav").value == "Pipeline"
    new = next(c for c in cards(app) if c["name"] == "Tania")
    assert (new["phone"], new["queue"], new["priority"]) == ("01911000999", "pipeline", "high")
    assert app.session_state["selected"] == new["id"]


def test_example_button_fills_the_form(app):
    go(app, "Add enquiry")
    click(app, "Banglish")
    assert "kobe shuru hobe" in app.text_area(key="add_msg").value


def test_ai_failure_falls_back_to_an_offline_guess(app):
    app.session_state["ai_clients"] = {}
    go(app, "Add enquiry")
    app.text_area(key="add_msg").set_value("hello I want the bootcamp").run()
    click(app, "Analyse with AI")
    assert not app.exception
    assert app.session_state["draft"]["meta"]["source"] == "offline"
    assert any("rough offline guess" in w.value for w in app.warning)


def test_weekly_summary_flags_invented_numbers(app):
    go(app, "Weekly summary")
    click(app, "weekly summary")
    assert any("57" in w.value for w in app.warning)                  # 57 is not in the data


def test_weekly_summary_falls_back_to_code(app):
    app.session_state["ai_clients"] = {}
    go(app, "Weekly summary")
    click(app, "weekly summary")
    assert any("enquiries came in" in m.value for m in app.markdown)


def test_missing_cache_shows_a_clear_banner(tmp_path, monkeypatch):
    monkeypatch.setattr(ai, "has_key", lambda p: False)
    monkeypatch.setattr(ai, "CACHE_PATH", tmp_path / "nope.json")
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60).run()
    assert not at.exception
    assert any("no saved AI result" in w.value for w in at.sidebar.warning)


def test_reset_restores_the_demo(app):
    app.button(key="open_p_1").click().run()
    app.button(key="contacted_1").click().run()
    click(app, "Reset demo data")
    assert next(c for c in cards(app) if c["id"] == 1)["status"] == "New"


# ---- unit tests for the new helpers
def test_unverified_numbers():
    facts = {"total": 19, "names": ["Rafi"], "p": {"New": 14}}
    assert summary.unverified_numbers("19 enquiries, 14 new, next 3 actions, 48 hours", facts) == []
    assert summary.unverified_numbers("We lost 57 and won 20", facts) == [20, 57]


def test_facts_only_contain_real_data():
    conn = db.connect()
    for r in ROWS:
        db.ingest_row(conn, r)
    for c in db.get_cards(conn):
        db.apply_analysis(conn, c["id"], {"kind": KEY[int(c["src_ids"].split(",")[0])]["kind"],
                                          "want": KEY[int(c["src_ids"].split(",")[0])]["want"]})
    f = summary.facts_from(db.get_cards(conn), rules.demo_now())
    assert f["total_enquiries"] == 19 and f["in_pipeline"] == 14
    assert f["asked_more_than_once"] == ["Rafi Ahmed"] and f["urgent_customers"] == ["Habibur Rahman"]
    assert len(f["waiting_48h_or_more"]) == 9
    assert sorted(f["not_sales"]) == ["job", "minor", "spam", "support", "urgent"]


def test_db_helpers():
    conn = db.connect()
    for r in ROWS:
        db.ingest_row(conn, r)
    assert db.count_messages_by_phone(conn, "01711000001") == 2 and db.count_messages_by_phone(conn, None) == 0
    db.update_latest(conn, 1, {"summary": "new", "reply": "hello"})
    c = db.get_cards(conn)[0]
    assert (c["summary"], c["reply"]) == ("new", "hello")


def test_no_lone_dash_lines_that_break_markdown(app):
    """Regression: a lone '-' under 'People' made it a giant heading, and '- / -' became a bullet.
    Uses the offline guess on purpose: it leaves company, role and headcount empty."""
    app.session_state["ai_clients"] = {}
    go(app, "Add enquiry")
    app.text_area(key="add_msg").set_value("hello I want the bootcamp").run()
    click(app, "Analyse with AI")
    for m in app.markdown:
        assert not re.search(r"\n-\s*($|/)", m.value), m.value
    assert any("People" in m.value for m in app.markdown)       # the cells really were rendered


def test_invalid_phone_is_warned_not_silently_dropped(app):
    go(app, "Add enquiry")
    app.text_input(key="add_phone").set_value("01234567890").run()
    app.text_area(key="add_msg").set_value(NEW_MSG).run()
    click(app, "Analyse with AI")
    assert any("not a valid Bangladeshi mobile number" in w.value for w in app.warning)


def test_pasted_enquiries_get_fresh_ids_not_sample_ids(app):
    go(app, "Add enquiry")
    app.text_area(key="add_msg").set_value(NEW_MSG).run()
    click(app, "Analyse with AI")
    click(app, "Save to CRM")
    new = next(c for c in cards(app) if c["name"] == "Tania")
    assert new["src_ids"] == "21"


def test_quick_contacted_button_on_the_card(app):
    app.button(key="quick_p_1").click().run()
    assert next(c for c in cards(app) if c["id"] == 1)["status"] == "Contacted"
    assert [m.value for m in app.metric][2] == "8"


def test_theme_is_not_alarm_red():
    cfg = (ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8")
    assert "primaryColor" in cfg and "#2563eb" in cfg
