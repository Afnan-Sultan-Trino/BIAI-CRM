"""Run the AI on the 20 sample messages and score it against tests/answer_key.json.

  python run_eval.py --providers groq --save-cache      # full run, also fills the demo cache
  python run_eval.py --providers gemini --ids 2,3,7,10,13,16,18,20   # Gemini free quota is small
  python run_eval.py --list-models groq                 # if a model name is rejected
"""
import argparse
import csv
import json
import sys
import time
from pathlib import Path

try:                                             # Windows certificate store (optional)
    import truststore
    truststore.inject_into_ssl()
except Exception:
    pass

from core import ai, evaluate

ROOT = Path(__file__).resolve().parent
try:
    sys.stdout.reconfigure(encoding="utf-8")      # Bangla text in the Windows console
except Exception:
    pass


def load(which="sample"):
    csv_name, key_name = (("sample-enquiries.csv", "answer_key.json") if which == "sample"
                          else ("holdout-enquiries.csv", "holdout_key.json"))
    rows = list(csv.DictReader(open(ROOT / "data" / csv_name, encoding="utf-8-sig")))
    key = json.loads((ROOT / "tests" / key_name).read_text(encoding="utf-8"))
    return rows, {int(k): v for k, v in key.items() if not k.startswith("_")}


def run_provider(name, rows, key, pace=True, judge=True):
    prov = ai.get_provider(name)
    if not ai.has_key(prov):
        print(f"[{name}] no key found ({prov.key_env}); skipped")
        return None
    client = ai.make_client(prov)
    print(f"\n=== {name} / {prov.model} : {len(rows)} messages ===")
    out, fails = {}, 0
    for i, r in enumerate(rows):
        rid = int(r["id"])
        try:
            a, meta = ai.analyse_message(r, prov, client)
        except ai.QuotaExhausted as e:
            print(f"#{rid}: daily quota exhausted, stopping {name}. {str(e)[:100]}")
            break
        except Exception as e:
            fails += 1
            print(f"#{rid}: FAILED {type(e).__name__}: {str(e)[:150]} | cause: {e.__cause__!r}")
            if fails >= 3:
                print("3 failures in a row: stopping. Run `python diagnose.py` to find the cause.")
                break
            continue
        fails = 0
        s = evaluate.score(r, a, key[rid])
        claims = []
        if judge:
            time.sleep(2)
            try:
                claims = ai.judge_reply(r, a.get("reply"), prov, client)
            except Exception as e:
                claims = [f"(judge failed: {type(e).__name__})"]
        out[rid] = {"analysis": a, "meta": meta, "score": s, "claims": claims}
        bad = [c for c, ok in s.items() if not ok]
        print(f"#{rid:<3}{meta['seconds']:>4}s  " + ("fields ok" if not bad else "WRONG: " + ", ".join(bad))
              + ("   | INVENTED FACT: " + "; ".join(claims) if claims else ""))
        if pace and i < len(rows) - 1:
            time.sleep(prov.gap_seconds)
    return out


def report(name, out, rows, key, tag=""):
    if not out:
        return
    t = evaluate.totals([v["score"] for v in out.values()])
    print(f"\n--- {name}: {t['n']} messages scored ---")
    for c in evaluate.CHECKS:
        print(f"{c:>13}: {t[c]}/{t['n']}")
    flagged = {rid: v["claims"] for rid, v in out.items() if v.get("claims")}
    print(f"\nReplies with an invented fact (second-AI fact check): {len(flagged)}/{t['n']}  {sorted(flagged)}")
    print("\nMistakes (AI vs key):")
    byid = {int(r["id"]): r for r in rows}
    for rid, v in out.items():
        a, k = v["analysis"], key[rid]
        for c in ("kind", "want", "lang"):
            if not v["score"][c]:
                print(f"  #{rid} {c}: AI={a.get(c)!r} key={k[c]!r}")
        if not v["score"]["phone"]:
            print(f"  #{rid} phone: AI={a.get('phone')!r} expected={evaluate.expected_phone(byid[rid])!r}")
        if not v["score"]["reply_safe"]:
            print(f"  #{rid} reply mentions a price or percentage: {a['reply'][:100]!r}")
    # file for you to READ (Bangla and Banglish replies need a human judge)
    lines = [f"# {name} results\n"]
    for rid, v in out.items():
        a = v["analysis"]
        lines.append(f"## #{rid} [{a['kind']} / {a['want']} / {a['lang']}] {byid[rid]['sender name']}\n"
                     f"> {byid[rid]['message']}\n\n- summary: {a['summary']}\n- reply: {a['reply']}\n"
                     + (f"- INVENTED FACT: {'; '.join(v['claims'])}\n" if v.get("claims") else ""))
    (ROOT / "tests" / f"results_{name}{tag}.md").write_text("\n".join(lines), encoding="utf-8")
    (ROOT / "tests" / f"results_{name}{tag}.json").write_text(
        json.dumps({str(k): v for k, v in out.items()}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nWrote tests/results_{name}{tag}.md (read the Bangla/Banglish replies yourself) and .json")


def rejudge(name, rows, key, tag):
    """Re-score and re-fact-check results you already have (fast: only the judge call runs)."""
    path = ROOT / "tests" / f"results_{name}{tag}.json"
    saved = json.loads(path.read_text(encoding="utf-8"))
    prov = ai.get_provider(name)
    client = ai.make_client(prov)
    byid = {int(r["id"]): r for r in rows}
    out = {}
    print(f"\n=== re-judging {len(saved)} saved results ({name}{tag}) ===")
    for rid_s, v in saved.items():
        rid = int(rid_s)
        a = ai.coerce(v["analysis"])
        try:
            claims = ai.judge_reply(byid[rid], a.get("reply"), prov, client)
        except Exception as e:
            claims = [f"(judge failed: {type(e).__name__})"]
        out[rid] = {"analysis": a, "meta": v["meta"], "score": evaluate.score(byid[rid], a, key[rid]), "claims": claims}
        print(f"#{rid:<3}" + ("clean" if not claims else "INVENTED FACT: " + "; ".join(claims)))
        time.sleep(2)
    report(name, out, rows, key, tag)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--providers", default="groq")
    ap.add_argument("--ids", default="")
    ap.add_argument("--save-cache", action="store_true")
    ap.add_argument("--list-models", default="")
    ap.add_argument("--no-judge", action="store_true")
    ap.add_argument("--rejudge", default="", help="provider name: re-score saved results, only the judge runs")
    ap.add_argument("--set", default="sample", choices=["sample", "holdout"],
                    help="holdout = 14 NEW messages the prompt was never tuned on")
    a = ap.parse_args()
    if a.list_models:
        prov = ai.get_provider(a.list_models)
        for m in ai.make_client(prov).models.list().data:
            print(m.id)
        return
    rows, key = load(a.set)
    evaluate.add_context(rows)
    tag = "" if a.set == "sample" else "_holdout"
    if a.ids:
        want = {int(x) for x in a.ids.split(",")}
        rows = [r for r in rows if int(r["id"]) in want]
    if a.rejudge:
        rejudge(a.rejudge, rows, key, tag)
        return
    cache = ai.load_cache()
    for name in a.providers.split(","):
        out = run_provider(name.strip(), rows, key, judge=not a.no_judge)
        report(name.strip(), out, rows, key, tag)
        if out and a.save_cache and a.set == "sample":
            byid = {int(r["id"]): r for r in rows}
            for rid, v in out.items():
                cache[ai.cache_key(byid[rid])] = {"analysis": v["analysis"], "meta": v["meta"]}
            ai.save_cache(cache)
            print(f"Saved {len(out)} results to data/analysis_cache.json")


if __name__ == "__main__":
    main()
