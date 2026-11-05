import csv
import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from core import ai, evaluate  # noqa: E402

KEY = {int(k): v for k, v in json.loads((ROOT / "tests" / "answer_key.json").read_text(encoding="utf-8")).items()
       if not k.startswith("_")}
ROWS = list(csv.DictReader(open(ROOT / "data" / "sample-enquiries.csv", encoding="utf-8-sig")))
PROV = ai.Provider("t", "http://x", "m", "T_KEY", 0)
GOOD = json.dumps({"kind": "lead", "want": "bootcamp", "lang": "banglish", "name": "Rafi Ahmed",
                   "phone": "01711000001", "headcount": None, "summary": "s", "reply": "Assalamu alaikum Rafi!"})


class Err(Exception):
    def __init__(self, code, msg="x"):
        super().__init__(msg)
        self.status_code = code


def fake(responses):
    calls = []

    def create(**kw):
        calls.append(kw)
        r = responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=r))])
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))), calls


def test_extract_json_variants():
    assert ai.extract_json('{"a":1}') == {"a": 1}
    assert ai.extract_json('```json\n{"a":1}\n```') == {"a": 1}
    assert ai.extract_json('Sure! Here it is: {"a": 1} hope that helps') == {"a": 1}
    assert ai.extract_json('[{"a":1}]') == {"a": 1}
    with pytest.raises(ValueError):
        ai.extract_json("no json here")


def test_coerce_tidies_types():
    a = ai.coerce({"kind": " Lead ", "want": "BOOTCAMP", "phone": "+8801711000001", "headcount": "40",
                   "name": None, "reply": " hi "})
    assert (a["kind"], a["want"], a["phone"], a["headcount"], a["name"], a["reply"]) == (
        "lead", "bootcamp", "01711000001", 40, "", "hi")
    assert ai.coerce({"phone": "12345"})["phone"] is None


def test_retry_then_success():
    client, calls = fake([Err(429, "slow down"), Err(503), GOOD])
    waits = []
    a, meta = ai.analyse_message(ROWS[0], PROV, client, sleep=waits.append)
    assert a["kind"] == "lead" and len(calls) == 3 and len(waits) == 2


def test_daily_quota_stops_immediately():
    client, calls = fake([Err(429, "Quota exceeded: GenerateRequestsPerDayPerProjectPerModel")])
    with pytest.raises(ai.QuotaExhausted):
        ai.analyse_message(ROWS[0], PROV, client, sleep=lambda s: None)
    assert len(calls) == 1


def test_json_mode_rejected_falls_back_to_plain():
    client, calls = fake([Err(400, "response_format not supported"), GOOD])
    a, _ = ai.analyse_message(ROWS[0], PROV, client, sleep=lambda s: None)
    assert a["kind"] == "lead" and "response_format" in calls[0] and "response_format" not in calls[1]


def test_user_message_carries_sender_phone_and_text():
    u = json.loads(ai.build_user_message(ROWS[1]))
    assert u["sender_name"] == "Tahmina Akter" and u["phone_field"] == "" and "ফেলোশিপ" in u["message"]


def test_analyse_uses_cache_without_calling_ai():
    cache = {ai.cache_key(ROWS[0]): {"analysis": {"kind": "lead"}, "meta": {"provider": "groq"}}}
    a, meta = ai.analyse(ROWS[0], cache=cache, clients={})
    assert a["kind"] == "lead" and meta["source"] == "cache"


def test_analyse_falls_back_when_ai_fails(monkeypatch):
    monkeypatch.setattr(ai, "has_key", lambda p: False)
    a, meta = ai.analyse(ROWS[10], cache={}, clients={})              # the crypto spam
    assert meta["source"] == "offline" and a["kind"] == "spam" and a["reply"] == ""
    a, meta = ai.analyse(ROWS[1], cache={}, clients={})
    assert a["lang"] == "bangla" and a["want"] == "fellowship" and "ধন্যবাদ" in a["reply"]


def test_analyse_tries_second_provider(monkeypatch):
    bad, _ = fake([Err(500), Err(500), Err(500), Err(500)])
    good, _ = fake([GOOD])
    a, meta = ai.analyse(ROWS[0], order=("groq", "gemini"), cache={}, clients={"groq": bad, "gemini": good},
                         sleep=lambda s: None)
    assert meta["provider"] == "gemini" and a["kind"] == "lead"


def perfect(row):
    k = KEY[int(row["id"])]
    return {"kind": k["kind"], "want": k["want"], "lang": k["lang"],
            "name": ai.clean.split_name(row["sender name"])["name"], "phone": evaluate.expected_phone(row),
            "reply": "" if k["kind"] == "spam" else {"bangla": "ধন্যবাদ", "banglish": "Apni ki interested? Amader team jogajog korbe.",
                                                      "english": "Thanks, our team will confirm."}[k["lang"]]}


def test_scorer_gives_full_marks_to_perfect_answers():
    t = evaluate.totals([evaluate.score(r, perfect(r), KEY[int(r["id"])]) for r in ROWS])
    assert all(t[c] == 20 for c in evaluate.CHECKS), t


def test_scorer_catches_the_traps():
    r = ROWS[1]                                                # Tahmina, no phone
    a = perfect(r) | {"phone": "01799999999", "kind": "lead", "want": "bootcamp",
                      "reply": "Fee is 5000 taka, 10% off"}
    s = evaluate.score(r, a, KEY[2])
    assert not s["phone"] and not s["want"] and not s["reply_safe"]
    spam = ROWS[10]
    assert not evaluate.score(spam, perfect(spam) | {"reply": "Hello!"}, KEY[11])["reply_present"]
    assert evaluate.score(ROWS[15], perfect(ROWS[15]) | {"kind": "lead"}, KEY[16])["kind"] is False
    assert evaluate.score(ROWS[7], perfect(ROWS[7]) | {"kind": "lead"}, KEY[8])["kind"] is True   # contested, accepted


def test_holdout_set_is_consistent():
    rows = list(csv.DictReader(open(ROOT / "data" / "holdout-enquiries.csv", encoding="utf-8-sig")))
    key = {int(k): v for k, v in json.loads((ROOT / "tests" / "holdout_key.json").read_text(encoding="utf-8")).items()
           if not k.startswith("_")}
    assert len(rows) == 14 and {int(r["id"]) for r in rows} == set(key)
    sample_msgs = {r["message"] for r in ROWS}
    assert not any(r["message"] in sample_msgs for r in rows)
    def ok(r):
        k = key[int(r["id"])]
        a = {"kind": k["kind"], "want": k["want"], "lang": k["lang"],
             "name": ai.clean.split_name(r["sender name"])["name"], "phone": evaluate.expected_phone(r),
             "reply": "" if k["kind"] == "spam" else {"bangla": "ধন্যবাদ", "banglish": "Apni ki interested? Amader team jogajog korbe.",
                                                       "english": "Thanks, our team will confirm."}[k["lang"]]}
        return evaluate.score(r, a, k)
    assert all(all(ok(r).values()) for r in rows)
    assert evaluate.expected_phone(rows[13]) == "01911000114"      # bad phone field, valid number in text
    assert evaluate.expected_phone(rows[0]) == "01898765432"      # Bangla digits in text


def test_repeat_asker_context():
    rows = evaluate.add_context([dict(r) for r in ROWS])
    assert [int(r["id"]) for r in rows if r["earlier_messages"]] == [12]
    assert json.loads(ai.build_user_message(rows[11]))["earlier_messages_from_this_person"] == 1
    assert json.loads(ai.build_user_message(rows[0]))["earlier_messages_from_this_person"] == 0


def test_cache_key_depends_on_prompt_version_and_context(monkeypatch):
    k1 = ai.cache_key(ROWS[0])
    assert ai.cache_key({**ROWS[0], "earlier_messages": 1}) != k1
    monkeypatch.setattr(ai, "PROMPT_VERSION", "v999")
    assert ai.cache_key(ROWS[0]) != k1


def test_judge_reply():
    client, _ = fake(['{"claims": ["says classes are live", "says recordings provided"]}'])
    assert ai.judge_reply(ROWS[3], "Class gula live hoy, recording o ache", PROV, client) == [
        "says classes are live", "says recordings provided"]
    client, calls = fake(['{"claims": []}'])
    assert ai.judge_reply(ROWS[3], "Team confirm korbe.", PROV, client) == []
    assert ai.judge_reply(ROWS[10], "", PROV, None) == []      # empty (spam) reply: no call made


def test_prompt_forbids_the_five_hallucinations_we_saw():
    p = ai.SYSTEM_PROMPT
    for phrase in ("live, recorded", "who a programme suits", "instalments", "what a term or add-on means",
                   "we can", "earlier_messages_from_this_person"):
        assert phrase in p, phrase


def test_setting_is_stripped(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "  gsk_abc \n")
    assert ai.get_setting("GROQ_API_KEY") == "gsk_abc"


def test_default_groq_model_and_windows_client(monkeypatch):
    monkeypatch.delenv("GROQ_MODEL", raising=False)
    assert ai.get_provider("groq").model in ("openai/gpt-oss-120b", ai.get_setting("GROQ_MODEL"))
    monkeypatch.setenv("GROQ_API_KEY", "k")
    monkeypatch.setenv("TLS_WORKAROUND", "1")
    c = ai.make_client(ai.get_provider("groq"))
    assert "api.groq.com" in str(c.base_url)


def test_prompt_has_no_sample_messages_copied_in():
    """The prompt must not contain the evaluation messages, or the 20/20 score means nothing."""
    p = ai.SYSTEM_PROMPT.lower()
    for r in ROWS:
        msg = r["message"].lower().strip()
        assert msg not in p, f"sample message #{r['id']} is quoted in the prompt"
    for bad in ("non-technical marketing manager", "two instalments", "lifetime access", "govt-approved"):
        assert bad not in p, bad


def test_odd_typography_is_normalised():
    a = ai.coerce({"reply": "3\u2011day bootcamp, call after 6\u202fpm\u00a0please", "summary": "2\u2011day workshop"})
    assert a["reply"] == "3-day bootcamp, call after 6 pm please" and a["summary"] == "2-day workshop"


def test_name_titles_ignored_by_scorer():
    row = {"sender name": "Prof. Anwar Hossain (Head of CSE, a private university)", "phone": "01911000108", "message": "x"}
    a = {"kind": "lead", "want": "corporate", "lang": "english", "name": "Anwar Hossain", "phone": "01911000108", "reply": "ok"}
    assert evaluate.score(row, a, {"kind": "lead", "want": "corporate", "lang": "english"})["name"]


def test_judge_prompt_does_not_flag_promises_to_follow_up():
    assert "NOT claims" in ai.JUDGE_PROMPT and "team will confirm" in ai.JUDGE_PROMPT


def test_holdout_104_names_the_bootcamp():
    key = json.loads((ROOT / "tests" / "holdout_key.json").read_text(encoding="utf-8"))
    assert key["104"]["want"] == "bootcamp"


def test_prompt_v3_rules_on_questions_and_timing():
    p = ai.SYSTEM_PROMPT
    assert ai.PROMPT_VERSION == "v4"
    assert "ONLY for corporate" in p and "ask NO question" in p and "Never promise WHEN" in p
    assert "preferred batch" not in p            # it implied batches exist


def test_prompt_v4_no_made_up_channel_purpose_or_promised_deliverables():
    p = ai.SYSTEM_PROMPT
    assert "Do NOT say what this channel is for" in p
    assert "Never promise a quote, proposal, discount or call" in p and "never agree to a time they suggest" in p
    assert "this channel is for course enquiries and" not in p
