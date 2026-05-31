#!/usr/bin/env python3
"""
Vivian's PM Skills Digest Generator v4
- Strict PM-skills focus (filters out general AI news)
- 7-day recency window for main cards
- 1-2 "thought provoker" items from creator-owned blogs/substacks only
- Quiet week banner when no recent results — still shows thought provokers
- shown_urls.json tracking to avoid reusing articles
- No markdown leaking into the page
"""
import os
import re
import json
import logging
import sys
from datetime import datetime, timedelta
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

# RSS feed for each thought leader. None = no feed, falls back to search.
THOUGHT_LEADERS = [
    ("Lenny Rachitsky",   "https://www.lennysnewsletter.com/feed"),
    ("Ethan Mollick",     "https://www.oneusefulthing.org/feed"),
    ("Simon Willison",    "https://simonwillison.net/atom/everything/"),
    ("Andrej Karpathy",   None),   # No RSS — DuckDuckGo fallback
    ("Swyx",              "https://www.latent.space/feed"),
    ("Andrew Ng",         "https://www.deeplearning.ai/the-batch/feed/"),
    ("Aakash Gupta",      "https://aakashg.substack.com/feed"),
    ("Harrison Chase",    "https://blog.langchain.dev/rss/"),
    ("Kristen Berman",    "https://kristenberman.substack.com/feed"),
    ("Shreyas Doshi",     "https://shreyasdoshi.substack.com/feed"),
    ("Julie Zhuo",        "https://joulee.medium.com/feed"),
    ("John Cutler",       "https://cutlefish.substack.com/feed"),
    ("Marty Cagan",       "https://www.svpg.com/feed/"),
    ("Pawel Huryn",       "https://huryn.substack.com/feed"),
    ("Karo Zieminski",    "https://karozieminski.substack.com/feed"),
    ("Josh Miller",       "https://josh.substack.com/feed"),
    ("Claire Vo",         "https://clairevo.substack.com/feed"),
    ("Amjad Masad",       "https://blog.replit.com/rss.xml"),
    ("Logan Kilpatrick",  "https://logankilpatrick.substack.com/feed"),
    ("Gergely Orosz",     "https://newsletter.pragmaticengineer.com/feed"),
    ("Deb Liu",           "https://debliu.substack.com/feed"),
]

# Subset used for thought provoker candidates — same RSS feeds, wider time window
TP_FEEDS = [feed for _, feed in THOUGHT_LEADERS if feed is not None]

SHOWN_URLS_PATH = os.path.join("digests", "shown_urls.json")


def load_shown_urls() -> list[str]:
    """Load the list of URLs already featured in previous digests."""
    try:
        with open(SHOWN_URLS_PATH) as f:
            data = json.load(f)
            if isinstance(data, list):
                return data
    except (FileNotFoundError, ValueError):
        pass
    return []


def save_shown_urls(urls: list[str]) -> None:
    """Persist the updated shown URLs list, keeping the last 200 entries."""
    os.makedirs("digests", exist_ok=True)
    # Cap at 200 to avoid the file growing forever
    trimmed = urls[-200:]
    with open(SHOWN_URLS_PATH, "w") as f:
        json.dump(trimmed, f, indent=2)


def extract_urls_from_html(html: str) -> list[str]:
    """Pull all href URLs out of generated digest HTML."""
    return re.findall(r'href="(https?://[^"]+)"', html)


def _strip_html(text: str) -> str:
    """Remove HTML tags and normalise whitespace."""
    return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', '', text or '')).strip()


def fetch_rss(name: str, url: str, max_age_days: int = 7, max_items: int = 3) -> list[dict]:
    """Return recent items from an RSS/Atom feed, filtered to max_age_days."""
    try:
        import feedparser
        from datetime import timezone
        feed = feedparser.parse(url)
        cutoff = datetime.now() - timedelta(days=max_age_days)
        results = []
        for entry in feed.entries:
            # Parse publish date
            pub = None
            for attr in ("published_parsed", "updated_parsed"):
                val = getattr(entry, attr, None)
                if val:
                    try:
                        pub = datetime(*val[:6])
                    except Exception:
                        pass
                    break

            if pub and pub < cutoff:
                continue  # too old

            snippet = _strip_html(
                getattr(entry, "summary", None) or
                getattr(entry, "description", None) or ""
            )[:400]

            results.append({
                "title":   entry.get("title", ""),
                "snippet": snippet,
                "url":     entry.get("link", ""),
                "date":    pub.strftime("%B %d, %Y") if pub else "",
            })

            if len(results) >= max_items:
                break

        return results
    except Exception as e:
        log.warning("RSS fetch failed for %s (%s): %s", name, url, e)
        return []


def search_fallback(query: str, max_results: int = 3) -> list[dict]:
    """DuckDuckGo search — used only for creators without an RSS feed."""
    try:
        from duckduckgo_search import DDGS
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results, timelimit="w"))
            return [
                {
                    "title":   r.get("title", ""),
                    "snippet": r.get("body", "")[:400],
                    "url":     r.get("href", ""),
                    "date":    "",
                }
                for r in results
            ]
    except Exception as e:
        log.warning("Search fallback failed for query '%s…': %s", query[:50], e)
        return []


def gather_recent() -> dict:
    log.info("Fetching recent creator content via RSS (last 7 days)...")
    findings = {}
    found_count = 0
    for name, feed_url in THOUGHT_LEADERS:
        if feed_url:
            results = fetch_rss(name, feed_url, max_age_days=7, max_items=3)
        else:
            # Fallback to search for creators with no RSS
            results = search_fallback(f'"{name}" AI product', max_results=3)
        findings[name] = results
        if results:
            found_count += 1
            log.info("  ✓ %-22s — %d item(s)", name, len(results))
        else:
            log.info("  ✗ %-22s — nothing recent", name)
    log.info("Gather complete: %d/%d thought leaders had recent content", found_count, len(THOUGHT_LEADERS))
    return findings


def gather_thought_provokers() -> list[dict]:
    log.info("Gathering thought provoker candidates via RSS (last 90 days)...")
    candidates = []
    seen_urls: set[str] = set()
    for name, feed_url in THOUGHT_LEADERS:
        if not feed_url:
            continue
        items = fetch_rss(name, feed_url, max_age_days=90, max_items=2)
        for item in items:
            if item["url"] not in seen_urls:
                seen_urls.add(item["url"])
                candidates.append(item)

    log.info("Thought provoker pool (90 days): %d candidates", len(candidates))

    # If still thin, widen to 365 days
    if len(candidates) < 6:
        log.info("Pool thin — widening to 365 days...")
        for name, feed_url in THOUGHT_LEADERS:
            if not feed_url:
                continue
            items = fetch_rss(name, feed_url, max_age_days=365, max_items=3)
            for item in items:
                if item["url"] not in seen_urls:
                    seen_urls.add(item["url"])
                    candidates.append(item)
        log.info("Thought provoker pool (365 days): %d candidates", len(candidates))

    return candidates[:20]


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


EVERGREEN_FALLBACK_TPS = [
    {
        "name": "Marty Cagan",
        "insight": "The biggest mistake product teams make is confusing output with outcome. Shipping features is not the same as solving problems — the best PMs obsess over the latter.",
        "takeaway": "Cagan's core thesis is the foundation of modern product thinking.",
        "url": "https://www.svpg.com/product-vs-feature-teams/",
    },
    {
        "name": "Shreyas Doshi",
        "insight": "Most PMs optimise for output metrics (features shipped, velocity) when they should be optimising for outcome metrics (customer behaviour change, business impact). The distinction determines whether you build a great product or just a busy roadmap.",
        "takeaway": "Reframing how you measure your own success is the highest-leverage PM habit.",
        "url": "https://twitter.com/shreyas/status/1276956836856393728",
    },
]


def build_digest_html(recent: dict, thought_provokers: list, shown_urls: list[str]) -> str:
    today = datetime.now().strftime("%B %d, %Y")

    # Build a deduplicated shown_urls note for Claude
    shown_urls_note = ""
    if shown_urls:
        shown_sample = shown_urls[-50:]  # last 50 is plenty
        shown_urls_note = f"""
━━━ ALREADY SHOWN IN PREVIOUS ISSUES — DO NOT REUSE ━━━
{json.dumps(shown_sample, indent=2)}
Pick articles whose URLs do NOT appear in the above list.
"""

    # If thought provokers are completely empty, use hardcoded evergreen cards
    if not thought_provokers:
        log.warning("Thought provoker search returned nothing — using evergreen fallback cards")
        tp_html = ""
        for tp in EVERGREEN_FALLBACK_TPS:
            tp_html += f"""<div class="thought-provoker">
  <div class="creator-header">
    <h2>{tp["name"]}</h2>
  </div>
  <div class="creator-headline">Worth your time regardless of when it was published</div>
  <div class="tldr"><span class="tldr-label">Why it's worth your time</span>{tp["takeaway"]}</div>
  <details><summary>Read more</summary>
  <div class="detail-body"><p>{tp["insight"]}</p></div>
  </details>
  <div class="creator-links"><a href="{tp["url"]}" target="_blank">Read →</a></div>
</div>
"""
        return f"""<div class="quiet-banner">
  <span class="quiet-icon">💤</span>
  <span>Quiet week — none of your thought leaders posted recently. Here are two evergreen reads worth your time.</span>
</div>
{tp_html}"""

    prompt = f"""You are building a PM intelligence digest for Vivian. Today is {today}.

OUTPUT RULES — READ CAREFULLY:
- Output ONLY valid HTML. Zero markdown. Zero code fences. Zero plain text outside HTML tags.
- Never output "Note:", "Consider:", asterisks, dashes, or any explanation.
- The digest MUST always contain at least 1-2 thought provoker cards. Never output an empty digest.

{shown_urls_note}

━━━ RECENT SEARCH RESULTS (last 7 days) ━━━
{json.dumps(recent, indent=2)}

━━━ THOUGHT PROVOKER CANDIDATES (last month / last year) ━━━
{json.dumps(thought_provokers, indent=2)}

━━━ STRICT FILTER — only include in creator cards ━━━
✓ Product management skills, frameworks, or decision-making
✓ How PMs should think about / work with AI
✓ Building AI-powered products from a PM perspective
✓ AI-native team structures, roadmapping, or discovery
✓ Prompting or agent workflows explained for product people
✓ PM career advice in the AI era

✗ EXCLUDE from creator cards: General AI model benchmarks or releases (no PM angle)
✗ EXCLUDE from creator cards: Pure coding tutorials
✗ EXCLUDE from creator cards: General tech news (Netflix, funding rounds, hardware)
✗ EXCLUDE from creator cards: Off-topic results (furniture, baby names, clinics, airlines)
✗ EXCLUDE any URL that appears in the ALREADY SHOWN list above

━━━ FORMAT — follow this EXACTLY ━━━

STEP 1 — Decide if there is qualifying creator content from the last 7 days.

If NO qualifying recent content: output the quiet-week banner first, then 1-2 thought provokers. No top-pick.
If YES: output creator cards, then thought provokers, then top-pick.

STEP 2 — Output the HTML using these exact class names and structure:

Quiet-week banner (only when nothing recent qualifies):
<div class="quiet-banner"><span class="quiet-icon">💤</span><span>Quiet week — none of your thought leaders posted in the last 7 days. Here are reads worth your time regardless.</span></div>

Creator card (one per qualifying creator — include a date badge using the article's publish date):
<div class="creator">
  <div class="creator-header"><h2>[NAME]</h2><span class="date-badge">[e.g. May 21]</span></div>
  <div class="creator-headline">[One sentence article subtitle]</div>
  <div class="tldr"><span class="tldr-label">TL;DR — PM Takeaway</span>[2-3 sentence PM takeaway — lead with the actionable insight]</div>
  <details><summary>Read more</summary>
  <div class="detail-body"><p>[2-3 paragraph summary of the article. Use &lt;ul&gt; lists for frameworks or numbered points.]</p></div>
  </details>
  <div class="creator-links"><a href="[URL]" target="_blank">Read →</a></div>
</div>

Thought provoker card (ALWAYS include 1-2; URL must not be in ALREADY SHOWN list):
<div class="thought-provoker">
  <div class="creator-header"><h2>[NAME]</h2><span class="date-badge">[date if known]</span></div>
  <div class="creator-headline">Worth your time regardless of when it was published</div>
  <div class="tldr"><span class="tldr-label">Why it's worth your time</span>[1-2 sentence takeaway]</div>
  <details><summary>Read more</summary>
  <div class="detail-body"><p>[1-2 paragraphs on the core idea]</p></div>
  </details>
  <div class="creator-links"><a href="[URL]" target="_blank">Read →</a></div>
</div>

Top-pick (only when creator cards exist — skip on quiet weeks):
<div class="top-pick">
  <div class="top-pick-label">🔥 Top pick this issue</div>
  <div class="top-pick-content">[2-3 sentences on why this is the most important read for an AI-native PM]</div>
</div>

Output nothing else. No notes. No markdown. No explanations."""

    try:
        log.info("Calling Claude Haiku for digest generation...")
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=4000,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as e:
        log.error("Claude API call failed: %s", e)
        raise

    html = response.content[0].text.strip()
    log.info("Claude returned %d characters", len(html))

    html = clean_claude_output(html)

    creator_count = html.count('class="creator"')
    tp_count = html.count('class="thought-provoker"')
    log.info("Digest contains %d creator card(s) and %d thought provoker(s)", creator_count, tp_count)

    # Safety net: if Claude produced no thought provokers, inject evergreen ones
    if tp_count == 0:
        log.warning("Claude produced 0 thought provokers — injecting evergreen fallback")
        banner = """<div class="quiet-banner"><span class="quiet-icon">💤</span><span>Quiet week — nothing new from your thought leaders. Here are reads worth your time regardless.</span></div>\n"""
        tp_html = ""
        for tp in EVERGREEN_FALLBACK_TPS:
            tp_html += f"""<div class="thought-provoker">
  <div class="creator-header"><h2>{tp["name"]}</h2></div>
  <div class="creator-headline">Worth your time regardless of when it was published</div>
  <div class="tldr"><span class="tldr-label">Why it's worth your time</span>{tp["takeaway"]}</div>
  <details><summary>Read more</summary>
  <div class="detail-body"><p>{tp["insight"]}</p></div>
  </details>
  <div class="creator-links"><a href="{tp["url"]}" target="_blank">Read →</a></div>
</div>
"""
        if not html or "<div" not in html or "no-updates" in html:
            html = banner + tp_html
        else:
            html = html + tp_html

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
      line-height: 1.65;
    }}

    .header {{
      background: #fff;
      border-bottom: 1px solid #e8e8e4;
      padding: 2.5rem 1.5rem 2rem;
      text-align: center;
    }}
    .header h1 {{ font-size: 1.4rem; font-weight: 700; letter-spacing: -0.3px; }}
    .header .tagline {{ font-size: 0.9rem; color: #888; margin-top: 0.3rem; }}
    .header .updated {{
      display: inline-block; margin-top: 0.85rem;
      font-size: 0.78rem; background: #f0f0ec; color: #666;
      padding: 0.25rem 0.85rem; border-radius: 999px;
    }}

    .container {{ max-width: 720px; margin: 0 auto; padding: 2rem 1.5rem 3rem; }}

    /* ── Quiet week banner ── */
    .quiet-banner {{
      display: flex;
      align-items: flex-start;
      gap: 0.75rem;
      background: #f7f7f4;
      border: 1px solid #e0ddd5;
      border-radius: 10px;
      padding: 1rem 1.25rem;
      margin-bottom: 1.25rem;
      font-size: 0.9rem;
      color: #666;
      line-height: 1.5;
    }}
    .quiet-icon {{ font-size: 1.2rem; flex-shrink: 0; margin-top: 0.1rem; }}

    /* ── Creator + thought-provoker cards ── */
    .creator, .thought-provoker {{
      background: #fff; border: 1px solid #e8e8e4;
      border-radius: 12px; padding: 1.25rem 1.4rem; margin-bottom: 0.9rem;
    }}
    .thought-provoker {{ background: #fffdf5; border-color: #e8d98a; }}

    .creator-header {{
      display: flex; align-items: flex-start;
      justify-content: space-between; gap: 10px; margin-bottom: 0.35rem;
    }}
    .creator-header h2 {{ font-size: 1rem; font-weight: 700; line-height: 1.3; }}
    .date-badge {{
      flex-shrink: 0; font-size: 0.7rem; font-weight: 500;
      color: #888; background: #f0f0ec;
      padding: 2px 8px; border-radius: 999px; white-space: nowrap;
    }}
    .badge-hot {{
      display: inline-block; background: #cc3300; color: #fff;
      font-size: 0.65rem; font-weight: 700; padding: 1px 6px;
      border-radius: 999px; margin-left: 5px; vertical-align: middle;
    }}
    .creator-headline {{
      font-size: 0.8rem; color: #777; font-style: italic; margin-bottom: 0.75rem;
    }}

    /* ── TL;DR block ── */
    .tldr {{
      background: #f0eeff; border-left: 3px solid #7b6ef6;
      border-radius: 0 6px 6px 0; padding: 0.5rem 0.85rem;
      font-size: 0.855rem; color: #2a2a2a; line-height: 1.55;
      margin-bottom: 0.6rem;
    }}
    .tldr-label {{
      font-size: 0.65rem; font-weight: 700; text-transform: uppercase;
      letter-spacing: 0.06em; color: #7b6ef6; display: block; margin-bottom: 3px;
    }}
    .thought-provoker .tldr {{ background: #fffbea; border-color: #c9a800; }}
    .thought-provoker .tldr-label {{ color: #a07800; }}

    /* ── Collapsible body ── */
    details {{ margin-top: 2px; }}
    summary {{
      cursor: pointer; font-size: 0.75rem; font-weight: 600; color: #7b6ef6;
      list-style: none; display: inline-flex; align-items: center; gap: 3px;
      user-select: none;
    }}
    summary::-webkit-details-marker {{ display: none; }}
    summary::before {{ content: '↓ '; font-size: 0.7rem; }}
    details[open] summary::before {{ content: '↑ '; }}
    details[open] summary {{ margin-bottom: 0.75rem; }}

    .detail-body p {{ font-size: 0.875rem; margin-bottom: 0.7rem; }}
    .detail-body ul, .detail-body ol {{
      font-size: 0.875rem; padding-left: 1.2rem; margin-bottom: 0.7rem;
    }}
    .detail-body li {{ margin-bottom: 0.4rem; }}
    .detail-body blockquote {{
      border-left: 3px solid #ddd; padding: 0.3rem 0.85rem;
      color: #555; font-style: italic; font-size: 0.855rem; margin: 0.5rem 0;
    }}

    .creator-links {{ margin-top: 0.6rem; font-size: 0.8rem; }}
    .creator-links a {{ color: #7b6ef6; text-decoration: none; font-weight: 500; }}
    .creator-links a:hover {{ text-decoration: underline; }}
    .creator-links span {{ color: #ccc; margin: 0 5px; }}

    /* ── Top pick ── */
    .top-pick {{
      background: #f0eeff; border: 1px solid #c4b8ff;
      border-radius: 12px; padding: 1.25rem 1.4rem; margin-top: 1.5rem;
    }}
    .top-pick-label {{
      font-size: 0.72rem; font-weight: 700; text-transform: uppercase;
      letter-spacing: 0.06em; color: #7b6ef6; margin-bottom: 0.5rem;
    }}
    .top-pick-content {{ font-size: 0.925rem; color: #1a1a1a; line-height: 1.55; }}

    /* ── No updates fallback ── */
    .no-updates {{
      text-align: center; padding: 3rem 1rem; font-size: 0.9rem; color: #aaa;
    }}

    .footer {{
      text-align: center; padding: 1.5rem;
      font-size: 0.78rem; color: #bbb; border-top: 1px solid #e8e8e4;
    }}

    @media (max-width: 600px) {{
      .header {{ padding: 1.5rem 1rem 1.25rem; }}
      .header h1 {{ font-size: 1.1rem; }}
      .container {{ padding: 1rem 0.85rem 2.5rem; }}
      .creator, .thought-provoker, .top-pick {{ padding: 1rem 1rem; border-radius: 10px; }}
      .creator-header {{ flex-direction: column; gap: 4px; }}
      .quiet-banner {{ font-size: 0.85rem; padding: 0.85rem 1rem; }}
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
    Updates every Monday &amp; Thursday &nbsp;·&nbsp; Tracking 21 PM &amp; AI thought leaders
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


def send_email(page_html: str, date_display: str) -> None:
    """Send the digest as an HTML email via Gmail SMTP."""
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    gmail_user = os.environ.get("GMAIL_USER", "viviankillin@gmail.com")
    gmail_app_password = os.environ.get("GMAIL_APP_PASSWORD", "")

    if not gmail_app_password:
        log.warning("GMAIL_APP_PASSWORD not set — skipping email send")
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"🧠 Vivian's PM Skills Digest — {date_display}"
    msg["From"]    = gmail_user
    msg["To"]      = gmail_user

    msg.attach(MIMEText(page_html, "html"))

    try:
        log.info("Sending digest email to %s...", gmail_user)
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(gmail_user, gmail_app_password)
            server.sendmail(gmail_user, gmail_user, msg.as_string())
        log.info("Email sent successfully")
    except Exception as e:
        # Email failure shouldn't abort the whole run — the HTML is already saved
        log.error("Email send failed: %s", e)


def main():
    log.info("━━━ Vivian's PM Skills Digest Generator v4 ━━━")
    start = datetime.now()

    try:
        # Load previously shown URLs to avoid repetition
        shown_urls = load_shown_urls()
        log.info("Loaded %d previously shown URLs", len(shown_urls))

        recent = gather_recent()
        thought_provokers = gather_thought_provokers()

        digest_html = build_digest_html(recent, thought_provokers, shown_urls)

        log.info("Building HTML page...")
        date_display = datetime.now().strftime("%A, %B %d, %Y")
        page = generate_page(digest_html)

        os.makedirs("digests", exist_ok=True)
        filename = f"digest-{datetime.now().strftime('%Y-%m-%d')}.html"
        output_path = os.path.join("digests", filename)

        with open(output_path, "w", encoding="utf-8") as f:
            f.write(page)

        update_manifest(filename)

        # Track which URLs appeared in this digest so future issues avoid them
        new_urls = extract_urls_from_html(digest_html)
        if new_urls:
            updated_urls = shown_urls + [u for u in new_urls if u not in shown_urls]
            save_shown_urls(updated_urls)
            log.info("Saved %d new URL(s) to shown_urls.json (%d total)", len(new_urls), len(updated_urls))

        # Send the same HTML as an email
        send_email(page, date_display)

        elapsed = (datetime.now() - start).seconds
        log.info("━━━ Done in %ds — saved to %s ━━━", elapsed, output_path)

    except Exception as e:
        log.error("━━━ DIGEST GENERATION FAILED ━━━")
        log.error("Error: %s", e)
        log.error("The GitHub Action will now exit with code 1 and send you a failure email.")
        sys.exit(1)


if __name__ == "__main__":
    main()
