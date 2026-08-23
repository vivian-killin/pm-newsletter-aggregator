#!/usr/bin/env python3
"""Measure the judge against a human baseline.

    python judge_eval.py                     # whatever JUDGE_MODEL is set to
    python judge_eval.py claude-haiku-4-5    # try a cheaper judge
    python judge_eval.py claude-opus-5 claude-sonnet-5 claude-haiku-4-5

Scores every article in judge-golden-labels.json with the given model(s) and
reports how often the judge agrees with the hand labels.

The number that matters is FALSE PASSES: articles you labelled FAIL that the
judge waves through. Each one is a bad card in the newsletter. A false fail
only costs you an article you would have enjoyed, and there is always another.
"""
import json, os, sys, types

# Import the pipeline without triggering a digest run.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import generate_digest as g

GOLDEN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "judge-golden-labels.json")
CACHE  = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".judge-article-cache.json")

# Comparing models means scoring the same 42 articles several times over. Cache
# the fetched bodies so every model is judged on identical input — otherwise a
# site that rate-limits on the second pass silently handicaps the second model.
_cache = json.load(open(CACHE, encoding="utf-8")) if os.path.exists(CACHE) else {}
_real_fetch = g.fetch_article_text


def _cached_fetch(url, max_chars=6000):
    if url not in _cache:
        text, source = _real_fetch(url, max_chars)
        _cache[url] = [text, source]
        json.dump(_cache, open(CACHE, "w", encoding="utf-8"))
    return tuple(_cache[url])


g.fetch_article_text = _cached_fetch


def run(model: str, labels: list[dict]) -> dict:
    g.JUDGE_MODEL = model
    rows = []
    for i, gold in enumerate(labels, 1):
        item = {"title": gold["title"], "url": gold["url"], "author": gold["author"],
                "feed_owner": gold["feed_owner"], "snippet": ""}
        v = g.score_item(item)
        judged = (v["score"] >= g.SCORE_THRESHOLD
                  and v["category"] != "neither"
                  and not v["manufactured_angle"]
                  and bool(v["pm_action"].strip()))
        rows.append({**gold, "judge_label": "PASS" if judged else "FAIL",
                     "judge_score": v["score"], "judge_reason": v["reason"],
                     "judge_manufactured": v["manufactured_angle"]})
        print(f"  {i:>2}/{len(labels)}  human={gold['human_label']:<4} "
              f"judge={'PASS' if judged else 'FAIL':<4} {gold['title'][:46]}", flush=True)
    return rows


def report(model: str, rows: list[dict]) -> tuple:
    agree       = [r for r in rows if r["human_label"] == r["judge_label"]]
    false_pass  = [r for r in rows if r["human_label"] == "FAIL" and r["judge_label"] == "PASS"]
    false_fail  = [r for r in rows if r["human_label"] == "PASS" and r["judge_label"] == "FAIL"]
    mae = sum(abs(r["score"] - r["judge_score"]) for r in rows) / len(rows)

    print(f"\n{'='*74}\n{model}\n{'='*74}")
    print(f"  agreement    {len(agree)}/{len(rows)}  ({100*len(agree)//len(rows)}%)")
    print(f"  FALSE PASS   {len(false_pass):>2}   <- bad cards that would reach the newsletter")
    print(f"  false fail   {len(false_fail):>2}   <- good articles dropped")
    print(f"  score MAE    {mae:.2f}   (mean absolute difference on the 0-5 score)")

    for label, rows_ in (("FALSE PASSES", false_pass), ("false fails", false_fail)):
        if not rows_:
            continue
        print(f"\n  {label}:")
        for r in rows_:
            print(f"    human {r['score']} / judge {r['judge_score']}  {r['title'][:52]}")
            print(f"       judge said: {r['judge_reason'][:96]}")
    return len(false_pass), len(agree) / len(rows), mae


if __name__ == "__main__":
    labels = json.load(open(GOLDEN, encoding="utf-8"))
    models = sys.argv[1:] or [g.JUDGE_MODEL]
    print(f"{len(labels)} hand-labelled articles "
          f"({sum(1 for l in labels if l['human_label']=='PASS')} PASS / "
          f"{sum(1 for l in labels if l['human_label']=='FAIL')} FAIL)\n")

    summary = []
    for m in models:
        print(f"--- scoring with {m} ---")
        rows = run(m, labels)
        summary.append((m, *report(m, rows)))
        out = f"judge-eval-{m}.json"
        json.dump(rows, open(out, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
        print(f"\n  full results -> {out}")

    if len(summary) > 1:
        print(f"\n{'='*74}\nSUMMARY  (pick on false passes first, agreement second)\n{'='*74}")
        print(f"{'model':<22}{'false pass':>12}{'agreement':>12}{'score MAE':>12}")
        for m, fp, acc, mae in summary:
            print(f"{m:<22}{fp:>12}{100*acc:>11.0f}%{mae:>12.2f}")
