#!/usr/bin/env python3
"""
Vivian's PM Skills Digest Generator v2
- Strict PM-skills focus (filters out general AI news)
- 3-day recency window for main cards
- 1-2 "thought provoker" items from older content, clearly labelled
- No markdown leaking into the page
"""
import os
import re
import json
import logging
import sys
from datetime import datetime
from anthropic import Anthropic

# ── Logging setup ──────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("digest")

client = Anthropic()

CREATORS = [
    ("Lenny Rachitsky",  "site:lennysnewsletter.com product management"),
    ("Ethan Mollick",    "site:oneusefulthing.org"),
    ("Simon Willison",   "site:simonwillison.net"),
    ("Andrej Karpathy",  '"Andrej Karpathy" AI product'),
    ("Swyx",             "site:latent.space"),
    ("Andrew Ng",        "site:deeplearning.ai/the-batch"),
    ("Aakash Gupta",     "site:aakashg.com OR \"Aakash Gupta\" product"),
    ("Harrison Chase",   "site:blog.langchain.dev"),
    ("Kristen Berman",   "site:kristenberman.substack.com"),
    ("Shreyas Doshi",    '"Shreyas Doshi" product'),
    ("Julie Zhuo",       '"Julie Zhuo" product management'),
    ("John Cutler",      "site:cutlefish.substack.com OR \"John Cutler\" product"),
    ("Marty Cagan",      "site:svpg.com"),
    ("Pawel Huryn",      "site:huryn.substack.com"),
    ("Karo Zieminski",   '"Karo Zieminski" product'),
    ("Josh Miller",      '"Josh Miller" product'),
    ("Claire Vo",        '"Claire Vo" product AI'),
    ("Amjad Masad",      "site:blog.replit.com OR \"Amjad Masad\" product"),
    ("Logan Kilpatrick", '"Logan Kilpatrick" AI product'),
    ("Gergely Orosz",    "site:newsletter.pragmaticengineer.com"),
]

THOUGHT_PROVOKER_QUERIES = [
    "site:svpg.com product management AI",
    "site:lennysnewsletter.com product strategy",
    "site:oneusefulthing.org AI work productivity",
    '"Shreyas Doshi" product framework',
    "site:cutlefish.substack.com product management",
]


def search(query: str, timelimit: str = "w", max_results: int = 3) -> list[dict]:
    try:
        from duckduckgo_search import DDGS
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results, timelimit=timelimit))
            return [
                {
                    "title":   r.get("title", ""),
                    "snippet": r.get("body", "")[:400],
                    "url":     r.get("href", ""),
                }
                for r in results
            ]
    except Exception as e:
        log.warning("Search failed for query '%s…': %s", query[:50], e)
        return []


def gather_recent() -> dict:
    log.info("Searching for recent creator content (last 7 days)...")
    findings = {}
    found_count = 0
    for name, query in CREATORS:
        results = search(query, timelimit="w", max_results=3)
        findings[name] = results
        if results:
            found_count += 1
            log.info("  ✓ %-22s — %d result(s)", name, len(results))
        else:
            log.info("  ✗ %-22s — no results", name)
    log.info("Recent search complete: %d/%d creators had results", found_count, len(CREATORS))
    return findings


def gather_thought_provokers() -> list[dict]:
    log.info("Finding thought provoker candidates (last month)...")
    candidates = []
    for q in THOUGHT_PROVOKER_QUERIES:
        hits = search(q, timelimit="m", max_results=2)
        candidates.extend(hits)
    log.info("Thought provoker pool: %d candidates", len(candidates[:12]))
    return candidates[:12]


def clean_claude_output(raw: str) -> str:
    """Strip code fences and any prose Claude wrote outside of HTML tags."""
    # Remove code fences
    raw = re.sub(r"```[a-z]*\n?", "", raw)
    raw = re.sub(r"```", "", raw)
    raw = raw.strip()

    # If the response contains HTML, keep only the portion from the first
    # opening tag to the last closing tag — this discards any conversational
    # prose Claude wrote before or after the HTML, regardless of its content.
    first_tag = raw.find("<")
    last_tag  = raw.rfind(">")
    if first_tag != -1 and last_tag != -1 and last_tag > first_tag:
        raw = raw[first_tag : last_tag + 1]

    return raw.strip()


def build_digest_html(recent: dict, thought_provokers: list) -> str:
    today = datetime.now().strftime("%B %d, %Y")

    prompt = f"""You are building a PM intelligence digest for Vivian. Today is {today}.

OUTPUT RULES — READ CAREFULLY:
- Output ONLY valid HTML. Zero markdown. Zero code fences. Zero plain text outside HTML tags.
- Never output "Note:", "Consider:", asterisks, dashes, or any explanation.
- If you have nothing to output, return exactly: <div class="no-updates">No new PM updates in the last 3 days.</div>

━━━ RECENT SEARCH RESULTS (last 7 days) ━━━
{json.dumps(recent, indent=2)}

━━━ THOUGHT PROVOKER CANDIDATES (last month) ━━━
{json.dumps(thought_provokers, indent=2)}

━━━ STRICT FILTER — only include content clearly about ━━━
✓ Product management skills, frameworks, or decision-making
✓ How PMs should think about / work with AI
✓ Building AI-powered products from a PM perspective
✓ AI-native team structures, roadmapping, or discovery
✓ Prompting or agent workflows explained for product people
✓ PM career advice in the AI era

✗ EXCLUDE: General AI model benchmarks or releases (no PM angle)
✗ EXCLUDE: Pure coding tutorials
✗ EXCLUDE: General tech news (Netflix, funding rounds, hardware)
✗ EXCLUDE: Off-topic results (furniture, baby names, clinics, airlines)
✗ EXCLUDE: Any result older than 3 days unless it becomes a thought provoker

━━━ FORMAT ━━━

For each creator with qualifying content from the last 3 days:
<div class="creator">
  <div class="creator-name">[NAME]</div>
  <div class="creator-insight">[1-2 sentences. Lead with the idea not the person. Name the framework, number, or claim specifically.]</div>
  <div class="creator-takeaway">AI-native PM angle: [one crisp sentence on why this matters for PMs]</div>
  <a class="creator-link" href="[URL]" target="_blank">Read →</a>
</div>

Pick exactly 1-2 thought provokers from the candidate list (must be PM-skills relevant):
<div class="thought-provoker">
  <div class="tp-label">Thought provoker — worth your time regardless of when it was published</div>
  <div class="creator-name">[NAME]</div>
  <div class="creator-insight">[1-2 sentences on the idea]</div>
  <div class="creator-takeaway">Why it's worth your time: [one sentence]</div>
  <a class="creator-link" href="[URL]" target="_blank">Read →</a>
</div>

End with one top-pick (choose the most actionable item from everything above):
<div class="top-pick">
  <div class="top-pick-label">Top pick</div>
  <div class="top-pick-content">[2-3 sentences on why this is the most important thing to read for an AI-native PM this week]</div>
</div>

Output nothing else. No notes. No markdown. No explanations. Only the HTML divs above."""

    # If every single search came back empty, skip the Claude call entirely
    total_results = sum(len(v) for v in recent.values())
    if total_results == 0:
        log.warning("All 20 searches returned empty — skipping Claude call, returning fallback")
        return '<div class="no-updates">No new updates found this issue. Check back next time.</div>'

    try:
        log.info("Calling Claude Haiku for digest generation...")
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=3000,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as e:
        log.error("Claude API call failed: %s", e)
        raise

    html = response.content[0].text.strip()
    log.info("Claude returned %d characters", len(html))

    # Belt-and-suspenders: strip code fences + stray markdown
    if html.startswith("```html"):
        html = html[7:]
    elif html.startswith("```"):
        html = html[3:]
    if html.endswith("```"):
        html = html[:-3]

    html = clean_claude_output(html)

    creator_count = html.count('class="creator"')
    tp_count = html.count('class="thought-provoker"')
    log.info("Digest contains %d creator card(s) and %d thought provoker(s)", creator_count, tp_count)

    if not html or "<div" not in html:
        log.warning("No valid HTML content generated — using fallback message")
        html = '<div class="no-updates">No new PM updates in the last 3 days. Check back next issue.</div>'

    return html


def generate_page(digest_html: str) -> str:
    date_display = datetime.now().strftime("%A, %B %d, %Y")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Vivian's PM Skills Digest — {date_display}</title>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: #fafaf8;
      color: #1a1a1a;
      line-height: 1.6;
    }}

    .header {{
      background: #fff;
      border-bottom: 1px solid #e8e8e4;
      padding: 2.5rem 1.5rem 2rem;
      text-align: center;
    }}
    .header h1 {{ font-size: 1.5rem; font-weight: 600; letter-spacing: -0.01em; }}
    .header .tagline {{ font-size: 0.9rem; color: #888; margin-top: 0.3rem; }}
    .header .updated {{
      display: inline-block; margin-top: 0.85rem;
      font-size: 0.78rem; background: #f0f0ec; color: #666;
      padding: 0.25rem 0.85rem; border-radius: 999px;
    }}

    .container {{ max-width: 680px; margin: 0 auto; padding: 2rem 1.5rem 3rem; }}

    /* ── Creator cards ── */
    .creator {{
      background: #fff; border: 1px solid #e8e8e4;
      border-radius: 10px; padding: 1.25rem 1.5rem; margin-bottom: 1rem;
    }}
    .creator-name {{
      font-size: 0.72rem; font-weight: 600; text-transform: uppercase;
      letter-spacing: 0.07em; color: #999; margin-bottom: 0.5rem;
    }}
    .creator-insight {{ font-size: 0.975rem; color: #1a1a1a; margin-bottom: 0.6rem; line-height: 1.55; }}
    .creator-takeaway {{
      font-size: 0.84rem; color: #555; background: #f7f7f4;
      border-left: 3px solid #c4b8ff; padding: 0.4rem 0.75rem;
      border-radius: 0 4px 4px 0; margin-bottom: 0.85rem; line-height: 1.5;
    }}
    .creator-link {{ font-size: 0.8rem; color: #7b6ef6; text-decoration: none; font-weight: 500; }}
    .creator-link:hover {{ text-decoration: underline; }}

    /* ── Thought provoker cards ── */
    .thought-provoker {{
      background: #fffdf5; border: 1px solid #e8d98a;
      border-radius: 10px; padding: 1.25rem 1.5rem; margin-bottom: 1rem;
    }}
    .tp-label {{
      font-size: 0.7rem; font-weight: 600; text-transform: uppercase;
      letter-spacing: 0.07em; color: #a07800; margin-bottom: 0.4rem;
    }}

    /* ── Top pick ── */
    .top-pick {{
      background: #f0eeff; border: 1px solid #c4b8ff;
      border-radius: 10px; padding: 1.25rem 1.5rem; margin-top: 2rem;
    }}
    .top-pick-label {{
      font-size: 0.72rem; font-weight: 600; text-transform: uppercase;
      letter-spacing: 0.07em; color: #7b6ef6; margin-bottom: 0.5rem;
    }}
    .top-pick-content {{ font-size: 0.975rem; color: #1a1a1a; line-height: 1.55; }}

    /* ── No updates fallback ── */
    .no-updates {{
      text-align: center; padding: 3rem 1rem;
      font-size: 0.9rem; color: #aaa;
    }}

    .footer {{
      text-align: center; padding: 1.5rem;
      font-size: 0.78rem; color: #bbb;
      border-top: 1px solid #e8e8e4;
    }}

    @media (max-width: 600px) {{
      .header {{ padding: 1.5rem 1rem 1.25rem; }}
      .header h1 {{ font-size: 1.2rem; }}
      .header .tagline {{ font-size: 0.82rem; }}
      .container {{ padding: 1rem 0.85rem 2.5rem; }}
      .creator, .thought-provoker, .top-pick {{
        padding: 1rem 1.1rem;
        border-radius: 8px;
      }}
      .creator-insight {{ font-size: 0.92rem; }}
      .creator-takeaway {{ font-size: 0.8rem; padding: 0.35rem 0.6rem; }}
      .creator-link {{ font-size: 0.82rem; }}
    }}
  </style>
</head>
<body>

  <div class="header">
    <h1>Vivian's PM Skills Digest</h1>
    <div class="tagline">What the best PM &amp; AI thinkers are saying</div>
    <div class="updated">Updated {date_display}</div>
  </div>

  <div class="container">
    {digest_html}
  </div>

  <div class="footer">
    Updates every Monday &amp; Thursday &nbsp;·&nbsp; Tracking 20 PM &amp; AI leaders
  </div>

</body>
</html>"""


def update_manifest(filename: str) -> None:
    manifest_path = os.path.join("digests", "manifest.json")
    try:
        with open(manifest_path) as f:
            manifest = json.load(f)
    except (FileNotFoundError, ValueError):
        manifest = []
    if filename not in manifest:
        manifest.append(filename)
        manifest.sort(reverse=True)
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)


def main():
    log.info("━━━ Vivian's PM Skills Digest Generator v2 ━━━")
    start = datetime.now()

    try:
        recent = gather_recent()
        thought_provokers = gather_thought_provokers()

        digest_html = build_digest_html(recent, thought_provokers)

        log.info("Building HTML page...")
        page = generate_page(digest_html)

        os.makedirs("digests", exist_ok=True)
        filename = f"digest-{datetime.now().strftime('%Y-%m-%d')}.html"
        output_path = os.path.join("digests", filename)

        with open(output_path, "w", encoding="utf-8") as f:
            f.write(page)

        update_manifest(filename)

        elapsed = (datetime.now() - start).seconds
        log.info("━━━ Done in %ds — saved to %s ━━━", elapsed, output_path)

    except Exception as e:
        log.error("━━━ DIGEST GENERATION FAILED ━━━")
        log.error("Error: %s", e)
        log.error("The GitHub Action will now exit with code 1 and send you a failure email.")
        sys.exit(1)


if __name__ == "__main__":
    main()
