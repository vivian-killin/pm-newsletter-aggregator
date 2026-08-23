#!/usr/bin/env python3
"""Turn paid judge runs into free permanent filters.

    python harvest_scores.py

Reads every digests/scores-*.json the judge has written and reports what it
learned, so you can bake the lessons in and switch the judge off.

Three kinds of finding, in descending order of how much money they save:

  1. Sources that never pass. Drop them from THOUGHT_LEADERS. Free, permanent,
     and it shrinks every future run.
  2. Recurring title shapes that always fail. Add a regex to _drop_reason.
     Free, permanent, catches the same junk forever.
  3. Sources that always pass. These are safe to keep, and tell you where the
     roster is actually earning its keep.

What this canNOT recover is the judge's per-article reading. A new off-topic
post from a source that usually passes has no pattern to catch it. That is the
residual you accept by turning the judge off.
"""
import json, glob, os, re, sys
from collections import defaultdict

D = os.path.dirname(os.path.abspath(__file__))
MIN_JUDGED = 3          # don't draw conclusions about a source from one article


def load() -> list[dict]:
    rows, files = [], sorted(glob.glob(os.path.join(D, "digests", "scores-*.json")))
    for f in files:
        try:
            rows += json.load(open(f, encoding="utf-8"))
        except Exception as e:
            print(f"  ! skipped {os.path.basename(f)}: {e}")
    print(f"{len(rows)} verdict(s) across {len(files)} run(s): "
          f"{', '.join(os.path.basename(f)[7:-5] for f in files)}\n")
    return rows


def passed(v: dict) -> bool:
    return (v["score"] >= 3 and v["category"] != "neither"
            and not v.get("manufactured_angle") and bool((v.get("pm_action") or "").strip()))


def main():
    rows = load()
    if not rows:
        print("No score logs yet. Let the judge run at least twice, then come back.")
        return

    by_source = defaultdict(list)
    for v in rows:
        by_source[v.get("feed_owner") or v.get("author") or "?"].append(v)

    dead  = {s: vs for s, vs in by_source.items()
             if len(vs) >= MIN_JUDGED and not any(passed(v) for v in vs)}
    solid = {s: vs for s, vs in by_source.items()
             if len(vs) >= MIN_JUDGED and all(passed(v) for v in vs)}

    print("=" * 74)
    print("1. SOURCES THAT NEVER PASSED  — delete these from THOUGHT_LEADERS")
    print("=" * 74)
    if not dead:
        print("  (none yet — every source has landed at least one article)")
    for s, vs in sorted(dead.items(), key=lambda kv: -len(kv[1])):
        avg = sum(v["score"] for v in vs) / len(vs)
        print(f"\n  {s}  —  0 of {len(vs)} passed, mean score {avg:.1f}")
        for v in vs[:3]:
            print(f"     {v['score']}/5  {v['title'][:56]}")
            print(f"          {v['reason'][:82]}")

    print("\n" + "=" * 74)
    print("2. RECURRING FAIL SHAPES  — candidates for a regex in _drop_reason")
    print("=" * 74)
    fails = [v for v in rows if not passed(v)]
    shapes = [
        ("podcast episode",   r"podcast|episode|with [A-Z][a-z]+ [A-Z]"),
        ("promo / housekeeping", r"discount|premium member|subscribe|announcing|now open"),
        ("community roundup", r"community wisdom|roundup|weekly digest|open thread"),
        ("personal essay",    r"\bmy (mom|dad|wife|husband|family)\b|cancer|grief"),
        ("news item",         r"\bnow (uses|supports|available)\b|launches|acquires|raises \$"),
    ]
    for name, pat in shapes:
        hit = [v for v in fails if re.search(pat, v["title"], re.I)]
        clean = [v for v in rows if passed(v) and re.search(pat, v["title"], re.I)]
        if len(hit) >= 2:
            verdict = "SAFE to filter" if not clean else f"CAREFUL — would also drop {len(clean)} passing"
            print(f"\n  {name}: {len(hit)} fail(s), {verdict}")
            for v in hit[:3]:
                print(f"     {v['title'][:62]}")

    print("\n" + "=" * 74)
    print("3. SOURCES THAT ALWAYS PASSED  — the roster is working here")
    print("=" * 74)
    for s, vs in sorted(solid.items(), key=lambda kv: -sum(v["score"] for v in kv[1]) / len(kv[1])):
        avg = sum(v["score"] for v in vs) / len(vs)
        print(f"  {avg:.1f} mean  {len(vs):>2} judged   {s}")

    kept = sum(1 for v in rows if passed(v))
    print(f"\noverall pass rate: {kept}/{len(rows)} ({100*kept//len(rows)}%)")
    print("\nAfter acting on sections 1 and 2, set JUDGE_MODEL=off to stop the spend.")
    print("The filters you just added keep working for free; what you give up is")
    print("the judge's read of any article those filters have never seen.")


if __name__ == "__main__":
    main()
