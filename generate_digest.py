#!/usr/bin/env python3
"""
PM Intel Digest Generator
Searches for recent content from 20 top PM & AI creators and generates a static HTML page.
Runs via GitHub Actions every Monday and Thursday at 2 PM MT.
"""
import os
import json
from datetime import datetime
from anthropic import Anthropic

client = Anthropic()

CREATORS = [
    ("Lenny Rachitsky",   "site:lennysnewsletter.com OR \"Lenny Rachitsky\""),
    ("Ethan Mollick",     "site:oneusefulthing.org OR \"Ethan Mollick\" AI"),
    ("Simon Willison",    "site:simonwillison.net"),
    ("Andrej Karpathy",   "\"Andrej Karpathy\" AI"),
    ("Swyx",              "site:latent.space OR swyx AI engineering"),
    ("Andrew Ng",         "site:deeplearning.ai OR \"Andrew Ng\" \"The Batch\""),
    ("Aakash Gupta",      "site:aakashg.com OR \"Aakash Gupta\" product"),
    ("Harrison Chase",    "site:blog.langchain.dev OR \"Harrison Chase\" LangChain"),
    ("Kristen Berman",    "site:kristenberman.substack.com OR \"Kristen Berman\" product"),
    ("Shreyas Doshi",     "\"Shreyas Doshi\" product management"),
    ("Julie Zhuo",        "site:joulee.medium.com OR \"Julie Zhuo\" product"),
    ("John Cutler",       "\"John Cutler\" product OR site:cutlefish.substack.com"),
    ("Marty Cagan",       "site:svpg.com OR \"Marty Cagan\""),
    ("Pawel Huryn",       "site:huryn.substack.com OR \"Pawel Huryn\" product"),
    ("Karo Zieminski",    "site:karo.substack.com OR \"Karo Zieminski\" product"),
    ("Josh Miller",       "\"Josh Miller\" \"The Browser Company\" OR arc browser"),
    ("Claire Vo",         "\"Claire Vo\" AI product"),
    ("Amjad Masad",       "site:blog.replit.com OR \"Amjad Masad\" AI"),
    ("Logan Kilpatrick",  "\"Logan Kilpatrick\" Google AI"),
    ("Gergely Orosz",     "site:newsletter.pragmaticengineer.com OR \"Gergely Orosz\""),
]


def search_creator(name: str, query: str) -> list[dict]:
    """Search DuckDuckGo for recent content from a creator."""
    try:
        from duckduckgo_search import DDGS
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=3, timelimit="m"))
            return [
                {
                    "title":   r.get("title", ""),
                    "snippet": r.get("body", "")[:400],
                    "url":     r.get("href", ""),
                }
                for r in results
            ]
    except Exception as e:
        print(f"  Search failed for {name}: {e}")
        return []


def gather_all_findings() -> dict:
    """Search for recent content from every creator."""
    print("Searching for recent creator content...")
    findings = {}
    for name, query in CREATORS:
        print(f"  → {name}")
        findings[name] = search_creator(name, query)
    return findings


def build_digest_html(findings: dict) -> str:
    """Pass search results to Claude Haiku and get back an HTML digest snippet."""
    today = datetime.now().strftime("%B %d, %Y")

    prompt = f"""You are building a PM intelligence digest for Vivian, a Product Manager learning to become AI-native.
Today is {today}.

Below are recent web search results for 20 top PM and AI creators.
Turn the useful ones into a clean HTML digest section.

SEARCH RESULTS:
{json.dumps(findings, indent=2)}

OUTPUT RULES:
- Return ONLY inner HTML — no <html>, <head>, or <body> tags.
- For each creator with genuinely new, useful content (skip anyone with nothing relevant):

<div class="creator">
  <div class="creator-name">[NAME]</div>
  <div class="creator-insight">[1-2 sentences. Lead with the idea, not "X posted about". Be specific: name the framework, the number, the claim.]</div>
  <div class="creator-takeaway">AI-native PM angle: [one crisp sentence on why this matters for PMs building or using AI]</div>
  <a class="creator-link" href="[URL]" target="_blank">Read →</a>
</div>

At the very end, add one top-pick block:

<div class="top-pick">
  <div class="top-pick-label">Top pick this week</div>
  <div class="top-pick-content">[The single most interesting or actionable insight across everyone. 2-3 sentences on why it matters for AI-native PMs.]</div>
</div>

Prioritise insights about: building AI products, prompting, agent workflows, AI tooling, AI product strategy.
Quality over quantity — only include creators with something genuinely worth reading."""

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=3000,
        messages=[{"role": "user", "content": prompt}],
    )
    html = response.content[0].text.strip()

    # Strip markdown code fences if Claude wrapped the output in them
    if html.startswith("```html"):
        html = html[7:]
    elif html.startswith("```"):
        html = html[3:]
    if html.endswith("```"):
        html = html[:-3]

    return html.strip()


def generate_page(digest_html: str) -> str:
    """Wrap the digest HTML snippet in a full styled page."""
    date_display = datetime.now().strftime("%A, %B %d, %Y")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>PM Intel Digest</title>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: #fafaf8;
      color: #1a1a1a;
      line-height: 1.6;
    }}

    /* ── Header ── */
    .header {{
      background: #fff;
      border-bottom: 1px solid #e8e8e4;
      padding: 2.5rem 1.5rem 2rem;
      text-align: center;
    }}
    .header h1 {{
      font-size: 1.5rem;
      font-weight: 600;
      letter-spacing: -0.01em;
    }}
    .header .tagline {{
      font-size: 0.9rem;
      color: #888;
      margin-top: 0.3rem;
    }}
    .header .updated {{
      display: inline-block;
      margin-top: 0.85rem;
      font-size: 0.78rem;
      background: #f0f0ec;
      color: #666;
      padding: 0.25rem 0.85rem;
      border-radius: 999px;
    }}

    /* ── Layout ── */
    .container {{
      max-width: 680px;
      margin: 0 auto;
      padding: 2rem 1.5rem 3rem;
    }}

    /* ── Creator cards ── */
    .creator {{
      background: #fff;
      border: 1px solid #e8e8e4;
      border-radius: 10px;
      padding: 1.25rem 1.5rem;
      margin-bottom: 1rem;
    }}
    .creator-name {{
      font-size: 0.72rem;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.07em;
      color: #999;
      margin-bottom: 0.5rem;
    }}
    .creator-insight {{
      font-size: 0.975rem;
      color: #1a1a1a;
      margin-bottom: 0.6rem;
      line-height: 1.55;
    }}
    .creator-takeaway {{
      font-size: 0.84rem;
      color: #555;
      background: #f7f7f4;
      border-left: 3px solid #c4b8ff;
      padding: 0.4rem 0.75rem;
      border-radius: 0 4px 4px 0;
      margin-bottom: 0.85rem;
      line-height: 1.5;
    }}
    .creator-link {{
      font-size: 0.8rem;
      color: #7b6ef6;
      text-decoration: none;
      font-weight: 500;
    }}
    .creator-link:hover {{ text-decoration: underline; }}

    /* ── Top pick ── */
    .top-pick {{
      background: #f0eeff;
      border: 1px solid #c4b8ff;
      border-radius: 10px;
      padding: 1.25rem 1.5rem;
      margin-top: 2rem;
    }}
    .top-pick-label {{
      font-size: 0.72rem;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.07em;
      color: #7b6ef6;
      margin-bottom: 0.5rem;
    }}
    .top-pick-content {{
      font-size: 0.975rem;
      color: #1a1a1a;
      line-height: 1.55;
    }}

    /* ── Footer ── */
    .footer {{
      text-align: center;
      padding: 1.5rem;
      font-size: 0.78rem;
      color: #bbb;
      border-top: 1px solid #e8e8e4;
    }}
  </style>
</head>
<body>

  <div class="header">
    <h1>PM Intel Digest</h1>
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
    """Keep digests/manifest.json up to date so the viewer sidebar works."""
    import json as _json
    manifest_path = os.path.join("digests", "manifest.json")
    try:
        with open(manifest_path) as f:
            manifest = _json.load(f)
    except (FileNotFoundError, ValueError):
        manifest = []

    if filename not in manifest:
        manifest.append(filename)
        manifest.sort(reverse=True)

    with open(manifest_path, "w") as f:
        _json.dump(manifest, f, indent=2)


def main():
    print("PM Intel Digest Generator")
    print("=" * 40)

    findings = gather_all_findings()

    print("\nGenerating digest with Claude Haiku...")
    digest_html = build_digest_html(findings)

    print("Building HTML page...")
    page = generate_page(digest_html)

    # Save to digests/ folder so the viewer can archive them
    os.makedirs("digests", exist_ok=True)
    filename = f"digest-{datetime.now().strftime('%Y-%m-%d')}.html"
    output_path = os.path.join("digests", filename)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(page)

    update_manifest(filename)

    print(f"\n✓ Done — saved to {output_path}")


if __name__ == "__main__":
    main()
