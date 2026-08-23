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
from typing import Literal

from anthropic import Anthropic
from pydantic import BaseModel, Field

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
# Every feed here was checked live on 2026-08-23: it resolves, it has dated
# entries, and it has published within the last ~120 days. Re-run that check
# before adding anyone — several previous entries were 404s or Substacks that
# had been silent for years, which quietly shrank the pool without any error.
#
# Cadence is deliberately mixed. High-frequency feeds (Simon Willison at ~19
# posts/week) will always dominate a "most recent N per creator" fetch, so the
# low-cadence PM voices are the ones that keep the digest on topic.
THOUGHT_LEADERS = [
    # ── PM craft: discovery, prioritisation, strategy ──────────────────────
    ("Marty Cagan",       "https://www.svpg.com/feed/"),
    ("Teresa Torres",     "https://www.producttalk.org/feed/"),
    ("Itamar Gilad",      "https://itamargilad.com/feed/"),
    ("Rich Mironov",      "https://www.mironov.com/feed/"),
    ("Shreyas Doshi",     "https://shreyasdoshi.substack.com/feed"),
    ("John Cutler",       "https://cutlefish.substack.com/feed"),
    ("Pawel Huryn",       "https://huryn.substack.com/feed"),

    # ── PM career, leadership and communication ────────────────────────────
    ("Nikhyl Singhal",    "https://theskip.substack.com/feed"),
    ("Ken Norton",        "https://www.bringthedonuts.com/feed.xml"),
    ("Wes Kao",           "https://newsletter.weskao.com/feed"),
    ("Jackie Bavaro",     "https://jackiebavaro.substack.com/feed"),
    ("Julie Zhuo",        "https://joulee.medium.com/feed"),
    ("Deb Liu",           "https://debliu.substack.com/feed"),

    # ── Growth, PLG and behavioural design ─────────────────────────────────
    ("Elena Verna",       "https://www.elenaverna.com/feed"),
    ("Leah Tharin",       "https://www.leahtharin.com/feed"),
    ("Kristen Berman",    "https://kristenberman.substack.com/feed"),
    ("Karo Zieminski",    "https://karozieminski.substack.com/feed"),

    # ── AI and product, and the engineering side PMs need to read ──────────
    ("Lenny Rachitsky",   "https://www.lennysnewsletter.com/feed"),
    ("Aakash Gupta",      "https://aakashgupta.substack.com/feed"),
    ("Peter Yang",        "https://creatoreconomy.so/feed"),
    ("Ethan Mollick",     "https://www.oneusefulthing.org/feed"),
    ("Gergely Orosz",     "https://newsletter.pragmaticengineer.com/feed"),
    ("Simon Willison",    "https://simonwillison.net/atom/everything/"),
    ("Swyx",              "https://www.latent.space/feed"),
]

# Removed 2026-08-23 after a live feed check, all producing nothing:
#   Andrew Ng (the-batch)      404
#   Amjad Masad (replit blog)  404
#   Harrison Chase (langchain) feed returns no entries
#   Logan Kilpatrick           feed returns no entries
#   Andrej Karpathy            no feed at all; the DuckDuckGo fallback was noise
#   Josh Miller                last post 2023-05-25
#   Claire Vo                  last post 2025-03-04 — she publishes in Lenny's
#                              newsletter now, so the byline logic surfaces her
#                              under her own name from Lenny's feed

def _feed(name: str, url: str) -> str:
    """Allow an alternate feed URL to override the one above, via env var
    FEED_<NAME> (e.g. FEED_AAKASH_GUPTA), set as a GitHub Actions secret.

    Note this does NOT solve paywalled posts. Substack's public RSS truncates
    every paid post at "Read more", and the private feed Substack documents is
    a private *podcast* feed, not a full-text post feed. For paid written
    posts, see fetch_paid_from_gmail below — the full text arrives by email.
    This override remains useful for a publication that genuinely offers a
    full-text feed, or to repoint a feed that moved.
    """
    key = "FEED_" + re.sub(r"[^A-Z0-9]+", "_", name.upper()).strip("_")
    return os.environ.get(key) or url


THOUGHT_LEADERS = [(n, _feed(n, u) if u else None) for n, u in THOUGHT_LEADERS]

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


# A link-blog quote post — the creator is quoting someone else, so the idea is
# not theirs and a card built from it misattributes the author.
QUOTE_POST_RE = re.compile(r"^\s*(quoting|link)\b\s*[:\s]", re.I)

# An automated or aggregated roundup rather than the creator's own writing
# (e.g. Latent Space's [AINews] firehose, which is most of that feed).
ROUNDUP_RE = re.compile(r"^\s*[\[(]?\s*(ainews|ai news|community wisdom|weekly roundup|this week in)", re.I)

# A release note / changelog for the creator's own tooling — not PM content.
RELEASE_RE = re.compile(r"^\s*[\w.-]+\s+v?\d+\.\d+", re.I)


def fetch_rss(name: str, url: str, max_age_days: int = 7, max_items: int = 3,
              min_age_days: int = 0) -> list[dict]:
    """Return feed items aged between min_age_days and max_age_days.

    Two things worth knowing:

    - min_age_days is a floor. max_age_days alone is only a ceiling, so
      "last 90 days" happily matches something published yesterday. Thought
      provokers need the floor or they are just this week's news relabelled.
    - Items are sorted newest-first before truncation, so max_items means
      "the N most recent", not "the first N the feed happened to list".
    """
    try:
        import feedparser
        feed = feedparser.parse(url)
        now    = datetime.now()
        oldest = now - timedelta(days=max_age_days)
        newest = now - timedelta(days=min_age_days)

        feed_author = ((feed.feed.get("author_detail") or {}).get("name")
                       or feed.feed.get("author") or name)

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

            if pub and pub < oldest:
                continue  # too old
            if pub and pub > newest:
                continue  # too new for this window
            if min_age_days and not pub:
                continue  # undated, so we cannot prove it is old enough

            title = entry.get("title", "") or ""

            # Substack guest posts carry dc:creator, which feedparser maps onto
            # entry.author. Without reading it, every guest post is attributed
            # to whoever owns the feed.
            author = ((entry.get("author_detail") or {}).get("name")
                      or entry.get("author") or feed_author)

            snippet = _strip_html(
                entry.get("summary") or entry.get("description") or ""
            )[:400]

            results.append({
                "title":      title,
                "snippet":    snippet,
                "url":        entry.get("link", "") or "",
                "date":       pub.strftime("%B %d, %Y") if pub else "",
                "feed_owner": name,
                "author":     author,
                "quote_post": bool(QUOTE_POST_RE.match(title)),
                "roundup":    bool(ROUNDUP_RE.match(title)),
                "release":    bool(RELEASE_RE.match(title)),
                "_sort":      pub or datetime.min,
            })

        results.sort(key=lambda r: r["_sort"], reverse=True)
        for r in results:
            r.pop("_sort", None)
        return results[:max_items]
    except Exception as e:
        log.warning("RSS fetch failed for %s (%s): %s", name, url, e)
        return []


def _drop_reason(item: dict) -> str | None:
    """Why this item can never be a card, regardless of what it says."""
    if item.get("quote_post"):
        return "quote post"
    if item.get("roundup"):
        return "automated roundup"
    if item.get("release"):
        return "release note"
    return None


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


# ══════════════════════════════════════════════════════════════════════════════
# PAID SUBSCRIPTIONS
#
# Substack's public RSS gives a preview and cuts off at "Read more" — measured
# on 2026-08-23, Aakash's paid posts came through at ~4-6k characters ending in
# "Upgrade to a paid subscriber ... Read more". The judge would be scoring a
# teaser, and the digest would be summarising one.
#
# The full text does arrive, by email, because Vivian pays for these. So for
# paid publications we read her inbox over IMAP instead of the feed, using the
# same app password already used to send the digest. No new secret, and
# imaplib/email are both standard library.
# ══════════════════════════════════════════════════════════════════════════════

# Roster name -> the domain that appears in that publication's post URLs.
PAID_SOURCES = {
    "Lenny Rachitsky": "lennysnewsletter.com",
    "Aakash Gupta":    "news.aakashg.com",
}


def fetch_paid_from_gmail(max_age_days: int = 7) -> dict:
    """Return {roster name: [item, ...]} for paid publications, read from email.

    Falls back to an empty dict on any failure, so a mail problem degrades to
    the RSS preview rather than killing the run.
    """
    import imaplib, email
    from email.header import decode_header, make_header

    user     = os.environ.get("GMAIL_USER", "viviankillin@gmail.com")
    password = os.environ.get("GMAIL_APP_PASSWORD", "")
    if not password:
        log.warning("GMAIL_APP_PASSWORD not set — paid posts will fall back to truncated RSS")
        return {}

    since  = (datetime.now() - timedelta(days=max_age_days)).strftime("%d-%b-%Y")
    found  = {name: [] for name in PAID_SOURCES}
    domains = {dom: name for name, dom in PAID_SOURCES.items()}

    try:
        with imaplib.IMAP4_SSL("imap.gmail.com") as imap:
            imap.login(user, password)
            # All Mail, not INBOX — newsletters are frequently auto-archived.
            imap.select('"[Gmail]/All Mail"', readonly=True)
            typ, data = imap.search(None, f'(SINCE "{since}")', '(FROM "substack.com")')
            ids = data[0].split() if typ == "OK" and data and data[0] else []
            log.info("Gmail: %d Substack message(s) since %s", len(ids), since)

            for mid in ids[-80:]:                      # newest 80 is plenty for a week
                typ, raw = imap.fetch(mid, "(RFC822)")
                if typ != "OK" or not raw or not raw[0]:
                    continue
                msg = email.message_from_bytes(raw[0][1])

                html = ""
                for part in msg.walk():
                    if part.get_content_type() == "text/html":
                        try:
                            html = part.get_payload(decode=True).decode(
                                part.get_content_charset() or "utf-8", "ignore")
                        except Exception:
                            pass
                        break
                if not html:
                    continue

                # The canonical post URL identifies the publication unambiguously,
                # where the From header varies between "lenny@substack.com" and
                # publication-branded senders.
                post_url, owner = "", None
                for href in re.findall(r'href="(https?://[^"]+)"', html):
                    for dom, name in domains.items():
                        if dom in href and "/p/" in href:
                            post_url = href.split("?")[0]
                            owner = name
                            break
                    if owner:
                        break
                if not owner:
                    continue

                subject = str(make_header(decode_header(msg.get("Subject", "")))).strip()
                try:
                    pub = email.utils.parsedate_to_datetime(msg.get("Date", "")).replace(tzinfo=None)
                except Exception:
                    pub = datetime.now()

                body = _strip_html(
                    re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html))

                found[owner].append({
                    "title":      subject,
                    "snippet":    body[:400],
                    "body":       body[:8000],   # the judge reads this, not the URL
                    "url":        post_url,
                    "date":       pub.strftime("%B %d, %Y"),
                    "feed_owner": owner,
                    # Guest posts: the email subject often carries "| Author",
                    # but the byline is unreliable here, so let the judge and
                    # the writer prompt read it off the body instead.
                    "author":     owner,
                    "quote_post": bool(QUOTE_POST_RE.match(subject)),
                    "roundup":    bool(ROUNDUP_RE.match(subject)),
                    "release":    bool(RELEASE_RE.match(subject)),
                    "_sort":      pub,
                })
    except Exception as e:
        log.warning("Gmail fetch failed (%s) — paid posts fall back to truncated RSS", e)
        return {}

    for name, items in found.items():
        items.sort(key=lambda r: r.pop("_sort"), reverse=True)
        log.info("  Gmail %-20s — %d full-text post(s)", name, len(items))
    return found


MAX_ITEMS_PER_CREATOR = 3


def gather_recent(shown_urls: list[str]) -> dict:
    log.info("Fetching recent creator content via RSS (last 7 days)...")
    shown = set(shown_urls)
    paid = fetch_paid_from_gmail(max_age_days=7)
    findings = {}
    found_count = 0
    for name, feed_url in THOUGHT_LEADERS:
        if paid.get(name):
            # Full text from email beats a paywalled RSS preview.
            candidates = paid[name]
        elif feed_url:
            # Over-fetch, then filter, then cap. Filtering after the cap would
            # let three quote posts crowd out a creator's one real article.
            candidates = fetch_rss(name, feed_url, max_age_days=7, max_items=12)
        else:
            # Fallback to search for creators with no RSS
            candidates = search_fallback(f'"{name}" AI product', max_results=3)
            for item in candidates:
                item.setdefault("feed_owner", name)
                item.setdefault("author", name)

        kept, dropped = [], []
        for item in candidates:
            if item["url"] in shown:
                dropped.append("already shown")
                continue
            reason = _drop_reason(item)
            if reason:
                dropped.append(reason)
                continue
            kept.append(item)
            if len(kept) >= MAX_ITEMS_PER_CREATOR:
                break

        findings[name] = kept
        if kept:
            found_count += 1
            extra = f" ({len(dropped)} filtered)" if dropped else ""
            log.info("  ✓ %-22s — %d item(s)%s", name, len(kept), extra)
        else:
            why = f" ({', '.join(sorted(set(dropped)))})" if dropped else ""
            log.info("  ✗ %-22s — nothing qualifying%s", name, why)

    log.info("Gather complete: %d/%d thought leaders had qualifying content",
             found_count, len(THOUGHT_LEADERS))
    return findings


# A thought provoker is pitched as "worth your time regardless of when it was
# published". Anything newer than this is just this week's news wearing a hat.
TP_MIN_AGE_DAYS = 30


def gather_thought_provokers(shown_urls: list[str], recent: dict) -> list[dict]:
    log.info("Gathering thought provoker candidates (%d-90 days old)...", TP_MIN_AGE_DAYS)
    shown = set(shown_urls)

    # Cross-pool dedup. Without this the same creator can headline a creator
    # card and a thought provoker in the same issue.
    recent_urls     = {i["url"] for items in recent.values() for i in items}
    recent_creators = {name for name, items in recent.items() if items}

    def collect(max_age_days: int, per_feed: int, seen: set) -> list[dict]:
        out = []
        for name, feed_url in THOUGHT_LEADERS:
            if not feed_url or name in recent_creators:
                continue
            kept = 0
            for item in fetch_rss(name, feed_url,
                                  max_age_days=max_age_days,
                                  max_items=per_feed * 6,
                                  min_age_days=TP_MIN_AGE_DAYS):
                url = item["url"]
                if url in seen or url in shown or url in recent_urls:
                    continue
                if _drop_reason(item):
                    continue
                seen.add(url)
                out.append(item)
                kept += 1
                if kept >= per_feed:
                    break
        return out

    seen_urls: set[str] = set()
    candidates = collect(90, 2, seen_urls)
    log.info("Thought provoker pool (90 days): %d candidates", len(candidates))

    # If still thin, widen to 365 days
    if len(candidates) < 6:
        log.info("Pool thin — widening to 365 days...")
        candidates += collect(365, 3, seen_urls)
        log.info("Thought provoker pool (365 days): %d candidates", len(candidates))

    return candidates[:20]


# ══════════════════════════════════════════════════════════════════════════════
# SCORING PASS
#
# Every candidate is scored on its own before anything is selected or written.
# Three things this buys that a prose filter inside the generation prompt did
# not:
#
#   1. The judge reads the ARTICLE, not a 400-character RSS snippet. Most bad
#      cards came from summarising something nobody had read.
#   2. Scores are thresholded in Python, so "drop it" is a code path rather
#      than an instruction the model can talk itself out of while it is also
#      trying to lay out HTML.
#   3. Every verdict is written to digests/scores-<date>.json. That file is
#      how you tune the rubric: read the misses, adjust RUBRIC, re-run.
#
# To change what counts as a good article, edit RUBRIC. Nothing else.
# ══════════════════════════════════════════════════════════════════════════════

RUBRIC = """An article PASSES if a working product manager can take something away and use it.
Exactly two kinds of article qualify:

  A. AI SKILLS A PM CAN ACTUALLY IMPLEMENT
     A practice, workflow, prompt, evaluation method, or way of working with AI
     that a PM could apply to their own job this week. The reader should finish
     it knowing what to DO, not merely what happened.

  B. PM CRAFT: SKILLS, FRAMEWORKS AND JUDGEMENT
     Product frameworks, prioritisation, discovery, strategy, metrics,
     stakeholder work, org design, career craft. The classic product-management
     canon, whether or not AI is involved.

Everything else FAILS. In particular these are never a pass:
  - Model releases, benchmarks, parameter counts, scaling laws, lab news
  - Funding rounds, acquisitions, industry gossip, general tech news
  - Infrastructure, harness architecture, or engineering-tooling deep dives
    written for engineers rather than for the people who plan the work
  - Automated news roundups and link aggregations
  - Personal essays with no product lesson

CALIBRATION — these are the anchors. Judge against them.

  FAIL: "[AINews] Death of Params: Z.ai CEO Jie Tang on GLM 5.3 and the new
        Post-training Scaling Laws" — model-scaling news. There is a PM
        implication you could bolt on ("smaller models change build-vs-buy"),
        and bolting it on is exactly the mistake. The article does not tell a
        PM what to do. FAIL.

  PASS: "The AI Productivity Paradox" (Marty Cagan) — teams ship faster with AI
        while outcomes stay flat, and here is why and what to change. It speaks
        directly to how a PM should run their team. PASS.

THE DECIDING TEST: can you name a specific thing this PM could do differently
after reading it, using only what the article itself says? If you have to
supply the product angle yourself, the article does not have one. Fail it."""


class ArticleVerdict(BaseModel):
    """One judge verdict. Kept small on purpose — long rubrics score worse."""
    category: Literal["ai-skill-a-pm-can-implement", "pm-craft-or-framework", "neither"]
    score: int = Field(ge=0, le=5, description="0 = irrelevant, 3 = usable, 5 = the best thing this week")
    pm_action: str = Field(description="The specific thing a PM could do differently after reading it, drawn only from the article. Empty string if there isn't one.")
    manufactured_angle: bool = Field(description="True if you had to supply the product relevance yourself rather than finding it in the article.")
    reason: str = Field(description="One sentence. Why this score.")


# Keep at or above this to survive. Tune from the score log, not from a hunch.
SCORE_THRESHOLD = 3

# Which model does the judging. Measured cost per run over ~42 candidates,
# at roughly 2.3k input and 400 output tokens each:
#
#   claude-opus-5     $5/$25 per MTok   ~$0.90/run   ~$7.80/month
#   claude-sonnet-5   $3/$15            ~$0.54/run   ~$4.70/month   <- current
#   claude-haiku-4-5  $1/$5             ~$0.18/run   ~$1.60/month
#
# Cheaper is not free: the subtle call is manufactured_angle, where the judge
# has to notice it invented the product relevance rather than found it. Before
# switching, run judge_eval.py against judge-golden-labels.json and look at the
# false-pass count — a false pass is a bad card in the newsletter, which is the
# whole thing this is meant to prevent. A false fail only costs you an article.
JUDGE_MODEL = os.environ.get("JUDGE_MODEL", "claude-sonnet-5")


def fetch_article_text(url: str, max_chars: int = 6000) -> tuple[str, str]:
    """Best-effort full article text. Returns (text, source) where source is
    'article' or 'snippet' so the score log records what the judge actually read."""
    import urllib.request
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (pm-digest)"})
        raw = urllib.request.urlopen(req, timeout=20).read().decode("utf-8", "ignore")
        body = re.sub(r"(?is)<(script|style|nav|header|footer)[^>]*>.*?</\1>", " ", raw)
        text = _strip_html(body)
        if len(text) > 400:
            return text[:max_chars], "article"
    except Exception as e:
        log.debug("Article fetch failed for %s: %s", url, e)
    return "", "snippet"


def score_item(item: dict) -> dict:
    """Judge one article on its own. Independent calls, deliberately — scoring a
    batch together lets the model grade on a curve against whatever else showed
    up that week rather than against the rubric."""
    if item.get("body"):
        body, source = item["body"], "email"     # full paid post, already in hand
    else:
        body, source = fetch_article_text(item["url"])
    if not body:
        body, source = item.get("snippet", ""), "snippet"

    prompt = f"""{RUBRIC}

━━━ THE ARTICLE ━━━
Title:  {item['title']}
Author: {item.get('author') or item.get('feed_owner')}
Source: {item.get('feed_owner')}
URL:    {item['url']}

{ {'article': 'Full text', 'email': 'Full text of the paid post, from email'}.get(source, 'RSS summary only (the full text could not be fetched — judge conservatively)') }:
{body}

Score it."""

    try:
        response = client.messages.parse(
            model=JUDGE_MODEL,
            max_tokens=4000,
            messages=[{"role": "user", "content": prompt}],
            output_format=ArticleVerdict,
        )
        v = response.parsed_output
        verdict = v.model_dump()
    except Exception as e:
        # A judge failure must not silently promote an article. Fail closed.
        log.warning("Scoring failed for %s: %s", item["url"][:60], e)
        verdict = {"category": "neither", "score": 0, "pm_action": "",
                   "manufactured_angle": False, "reason": f"scoring error: {e}"}

    verdict.update({"title": item["title"], "url": item["url"],
                    "author": item.get("author"), "feed_owner": item.get("feed_owner"),
                    "judged_on": source})
    return verdict


def score_pool(items: list[dict], label: str) -> tuple[list[dict], list[dict]]:
    """Score every item, return (survivors, all verdicts)."""
    if not items:
        return [], []
    log.info("Scoring %d %s candidate(s) with %s...", len(items), label, JUDGE_MODEL)
    survivors, verdicts = [], []
    for item in items:
        v = score_item(item)
        verdicts.append(v)
        passed = (v["score"] >= SCORE_THRESHOLD
                  and v["category"] != "neither"
                  and not v["manufactured_angle"]
                  and bool(v["pm_action"].strip()))
        if passed:
            item["judge"] = v
            survivors.append(item)
        mark = "PASS" if passed else "fail"
        log.info("  [%s] %d/5 %-28s %s", mark, v["score"], v["category"], item["title"][:44])
    log.info("%s: %d of %d survived", label, len(survivors), len(items))
    return survivors, verdicts


def save_scores(verdicts: list[dict]) -> None:
    """Persist every verdict, kept and dropped alike. This is the tuning loop."""
    os.makedirs("digests", exist_ok=True)
    path = os.path.join("digests", f"scores-{datetime.now().strftime('%Y-%m-%d')}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(sorted(verdicts, key=lambda v: -v["score"]), f, indent=2, ensure_ascii=False)
    log.info("Wrote %d verdict(s) to %s", len(verdicts), path)


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

    # If thought provokers are completely empty, use hardcoded evergreen cards.
    # Only when there is no recent content either — otherwise this path would
    # discard perfectly good creator cards just because the TP pool came up dry.
    if not thought_provokers and not any(recent.values()):
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

    total_leaders = len(THOUGHT_LEADERS)
    leaders_with_content = sum(1 for v in recent.values() if v)

    prompt = f"""You are building a PM intelligence digest for Vivian. Today is {today}.
{total_leaders} thought leaders are tracked. {leaders_with_content} had new content in the last 7 days.

OUTPUT RULES — READ CAREFULLY:
- Output ONLY valid HTML. Zero markdown. Zero code fences. Zero plain text outside HTML tags.
- Never output "Note:", "Consider:", asterisks, dashes, or any explanation.
- Include a card ONLY if it genuinely passes the filter below. A short issue is the
  correct output on a quiet week — do NOT pad to fill space. Two strong cards beat
  five weak ones, and one strong card beats two weak ones.
- If nothing qualifies, output the quiet-week banner on its own. An honest short
  issue is a successful result, not a failure.

{shown_urls_note}

━━━ RECENT RSS RESULTS (last 7 days) ━━━
{json.dumps(recent, indent=2)}

━━━ THOUGHT PROVOKER CANDIDATES (last 90–365 days) ━━━
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
✗ EXCLUDE: Model releases, benchmarks, parameter counts, or lab/funding news
✗ EXCLUDE: Anything whose PM relevance you would have to invent. If the source does not
   itself speak to product decisions, DROP IT. Do not manufacture a PM angle for an
   article that has none — that is the single worst failure this digest can make.
✗ EXCLUDE from creator cards: Off-topic results (furniture, baby names, clinics, airlines)
✗ EXCLUDE any URL that appears in the ALREADY SHOWN list above

━━━ ATTRIBUTION — GET THE BYLINE RIGHT ━━━
Every item carries "feed_owner" (whose feed it arrived on) and "author" (who actually
wrote it). These are frequently DIFFERENT — guest posts are common.
- The card's <h2> MUST be the "author" value, never "feed_owner".
- Write the summary about what the AUTHOR said. Never write "<feed_owner> tested X"
  about a piece that someone else wrote for <feed_owner>'s newsletter.
- Never credit the author with an idea they are quoting from a third party.
- Never merge two different items into one card. Each card summarises exactly one
  URL, and every claim in it must come from that URL.
- If "author" is missing or empty, fall back to "feed_owner".

━━━ FORMAT — output in THIS EXACT ORDER ━━━

STEP 1 — Decide which items genuinely qualify. Be strict. Dropping every item is allowed.
If NO qualifying recent content: output quiet-week banner, then 0-2 thought provokers. Stop.
If YES: follow steps 2-6 in order, using ONLY the items that qualified.

STEP 2 — Top pick FIRST (only when qualifying creator content exists):
<div class="top-pick">
  <div class="top-pick-label">🔥 Top pick this issue</div>
  <div class="top-pick-content">[2-3 sentences: what it is, why it's the most important read for an AI-native PM this week. Name the author and the specific insight.]</div>
</div>

STEP 3 — Meta stats line:
<div class="digest-meta">[N] thought leaders tracked &nbsp;·&nbsp; [N] had updates this issue</div>

STEP 4 — Jump nav (one pill per qualifying creator, plus a Thought Provokers pill at the end):
<nav class="digest-nav">
  <a href="#[slug]">[Creator Name]</a>
  <a href="#thought-provokers" class="tp-nav">📚 Thought Provokers</a>
</nav>
Use the creator's lowercased first name or surname as the slug (e.g. "lenny", "cutler", "mollick").

STEP 5 — Creator cards (one per qualifying creator, id must match the nav slug):
<div class="creator" id="[slug]">
  <div class="creator-header"><h2>[AUTHOR — the item's "author" field, not "feed_owner"]</h2><span class="date-badge">[e.g. May 21]</span></div>
  <div class="creator-headline">[One-sentence article subtitle]</div>
  <div class="tldr"><span class="tldr-label">TL;DR — PM Takeaway</span>[2-3 sentence PM takeaway — lead with the actionable insight]</div>
  <details><summary>Read more</summary>
  <div class="detail-body"><p>[2-3 paragraph summary. Use &lt;ul&gt; for frameworks or numbered points.]</p></div>
  </details>
  <div class="creator-links"><a href="[URL]" target="_blank">Read →</a></div>
</div>

STEP 6 — Thought provoker cards (0-2, only ones genuinely worth it; id="thought-provokers" on the first one):
<div class="thought-provoker" id="thought-provokers">
  <div class="creator-header"><h2>[NAME]</h2><span class="date-badge">[date if known]</span></div>
  <div class="creator-headline">Worth your time regardless of when it was published</div>
  <div class="tldr"><span class="tldr-label">Why it's worth your time</span>[1-2 sentence takeaway]</div>
  <details><summary>Read more</summary>
  <div class="detail-body"><p>[1-2 paragraphs on the core idea]</p></div>
  </details>
  <div class="creator-links"><a href="[URL]" target="_blank">Read →</a></div>
</div>
(Additional thought provoker cards use class="thought-provoker" without the id.)

Quiet-week banner (only when nothing recent qualifies — replaces steps 2-4):
<div class="quiet-banner"><span class="quiet-icon">💤</span><span>Quiet week — none of your thought leaders posted in the last 7 days. Here are reads worth your time regardless.</span></div>

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

    # Safety net: only when the issue is completely empty. A thin issue that has
    # real creator cards and no thought provoker is a valid outcome — injecting
    # filler there is exactly the padding this digest is trying to stop.
    if creator_count == 0 and tp_count == 0:
        log.warning("Claude produced an empty digest — injecting evergreen fallback")
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

    /* ── Digest meta line ── */
    .digest-meta {{
      font-size: 0.8rem; color: #888; margin-bottom: 1rem;
    }}

    /* ── Jump nav ── */
    .digest-nav {{
      display: flex; flex-wrap: wrap; gap: 6px;
      margin-bottom: 1.5rem;
    }}
    .digest-nav a {{
      display: inline-block; padding: 4px 11px;
      font-size: 0.75rem; font-weight: 500; color: #444;
      background: #fff; border: 1px solid #ddd;
      border-radius: 999px; text-decoration: none;
      transition: background 0.12s, border-color 0.12s;
    }}
    .digest-nav a:hover {{ background: #1a1a1a; color: #fff; border-color: #1a1a1a; }}
    .digest-nav a.tp-nav {{ background: #fffdf5; border-color: #e8d98a; color: #a07800; }}
    .digest-nav a.tp-nav:hover {{ background: #a07800; color: #fff; border-color: #a07800; }}

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

    /* ── Top pick (appears at top of digest) ── */
    .top-pick {{
      background: #f0eeff; border: 1px solid #c4b8ff;
      border-radius: 12px; padding: 1.25rem 1.4rem; margin-bottom: 1.25rem;
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
    Updates every Monday &amp; Thursday &nbsp;·&nbsp; Tracking {len(THOUGHT_LEADERS)} PM &amp; AI thought leaders
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

        recent = gather_recent(shown_urls)
        thought_provokers = gather_thought_provokers(shown_urls, recent)

        # Score every candidate before anything is selected or written.
        all_verdicts = []
        for name, items in list(recent.items()):
            kept, verdicts = score_pool(items, f"recent/{name}")
            recent[name] = kept
            all_verdicts += verdicts
        thought_provokers, tp_verdicts = score_pool(thought_provokers, "thought provokers")
        all_verdicts += tp_verdicts
        save_scores(all_verdicts)

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
