"""Keep only the saved AI results that match the current prompt, and drop stale ones.
Run once after changing the prompt:   python prune_cache.py        (no API calls)"""
import csv
from pathlib import Path

from core import ai, evaluate

ROOT = Path(__file__).resolve().parent
rows = evaluate.add_context(list(csv.DictReader(open(ROOT / "data" / "sample-enquiries.csv", encoding="utf-8-sig"))))
keep = {ai.cache_key(r) for r in rows}
cache = ai.load_cache(ai.CACHE_PATH)
pruned = {k: v for k, v in cache.items() if k in keep}
ai.save_cache(pruned, ai.CACHE_PATH)
print(f"cache: {len(cache)} -> {len(pruned)} entries ({len(keep)} expected for the 20 samples)")
if len(pruned) < len(keep):
    print("Some samples have no saved result: run  python run_eval.py --providers groq --save-cache")
