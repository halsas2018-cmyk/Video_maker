"""
news_fetcher.py
Step 1 of the Shorts pipeline: discover and rank trending topics.

Uses only `requests` + stdlib XML parsing (no feedparser dependency).
Free / zero-cost — RSS feeds, the Hacker News public API, and YouTube
channel RSS feeds (which also give us real view counts for engagement).
"""

import re
import time
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from collections import defaultdict

# ---------------------------------------------------------------------------
# 1. Sources — edit this list freely. Add/remove feeds as your niche shifts.
# ---------------------------------------------------------------------------

RSS_FEEDS = {
    "OpenAI Blog": "https://openai.com/blog/rss.xml",
    "Google AI Blog": "https://blog.google/technology/ai/rss/",
    "TechCrunch AI": "https://techcrunch.com/category/artificial-intelligence/feed/",
    "The Verge AI": "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml",
    "Ars Technica": "https://feeds.arstechnica.com/arstechnica/technology-lab",
    "MIT Tech Review": "https://www.technologyreview.com/feed/",
    "VentureBeat AI": "https://venturebeat.com/category/ai/feed/",
    "arXiv cs.AI": "https://export.arxiv.org/rss/cs.AI",
    # Google News RSS — one query per category for proper tagging
    "Google News AI": "https://news.google.com/rss/search?q=AI+artificial+intelligence&hl=en-US&gl=US&ceid=US:en",
    "Google News Business": "https://news.google.com/rss/search?q=business+startup+funding&hl=en-US&gl=US&ceid=US:en",
    "Google News Science": "https://news.google.com/rss/search?q=science+research+breakthrough&hl=en-US&gl=US&ceid=US:en",
    # Reddit RSS feeds (only ones that work reliably)
    "Reddit r/programming": "https://old.reddit.com/r/programming/top/.rss",
    # Business / Finance feeds (Part 2 of UPGRADE_PLAN.md)
    "CNBC Business": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10001147",
    "TechCrunch Startups": "https://techcrunch.com/category/startups/feed/",
    "MarketWatch": "https://feeds.marketwatch.com/marketwatch/topstories/",
}

# ---------------------------------------------------------------------------
# YouTube channel RSS feeds — static channel IDs, no resolver complexity.
#
# Each channel ships ~15 recent videos with REAL view counts
# (media:group/media:statistics@views), so YouTube stories get genuine
# engagement scoring instead of the neutral-RSS default.
#
# To find a channel ID from its @handle (a bare curl hits Google's cookie-
# consent wall; the browser UA + SOCS=CAI cookie bypass it):
#   curl -sL -A "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36" \
#     -H "Cookie: SOCS=CAI" "https://www.youtube.com/@HANDLE/about" | grep -o '"externalId":"[^"]*"' | head -1
#
# Parked candidates (verify the ID with the command above, then activate):
#   "YouTube TheAIGRID":     "<paste-channel-id>",   # AI-native daily news
#   "YouTube AI Explained":  "<paste-channel-id>",   # AI-native deep dives
#   "YouTube CNBC Make It":  "UCH5_L3ytGbBziX0CLuYdQ1Q",  # ID extracted but feed 404s — re-verify
#   "YouTube The Economist": "UC0p5jTq6Xx_DosDFxVXnWaQ",  # ID extracted, feed never tested — verify first
# A wrong/dead ID fails soft: fetch_youtube_feed logs "[skip]" and moves on.
# ---------------------------------------------------------------------------
YOUTUBE_CHANNELS = {
    "YouTube CNBC": "UCrp_UI8XtuYfpiqluWLD7Lw",           # CNBC Television — markets/earnings interviews
    "YouTube Bloomberg Tech": "UCIALMKvObZNtJ6AmdCLP7Lg", # Bloomberg Technology — tech + AI coverage
    "YouTube Yahoo Finance": "UCEAZeUIeJs0IjQiqTCdVSIg",  # broad business coverage
}
YOUTUBE_FEED_URL = "https://www.youtube.com/feeds/videos.xml?channel_id={}"

# Hacker News: used as a proxy for "viral discussions" / social engagement signal
HN_TOP_STORIES_URL = "https://hacker-news.firebaseio.com/v0/topstories.json"
HN_ITEM_URL = "https://hacker-news.firebaseio.com/v0/item/{}.json"
# Widened: AI terms + business/finance terms (Part 2 of UPGRADE_PLAN.md)
HN_KEYWORDS = re.compile(
    r"\b("
    r"ai|llm|gpt|model|openai|anthropic|google|robot|chip|nvidia|"
    r"business|finance|market|stock|earnings|startup funding|crypto|bitcoin|"
    r"economy|inflation|layoff|acquisition|ipo|revenue|fed|interest rate"
    r")\b",
    re.I,
)

# Keywords that define "relevance to the niche" — tune this to your channel
# Widened: existing AI terms + business/finance terms (Part 2 of UPGRADE_PLAN.md)
NICHE_KEYWORDS = [
    # AI / Tech terms (existing)
    "ai", "artificial intelligence", "llm", "gpt", "openai", "anthropic",
    "google deepmind", "model", "chatbot", "robot", "chip", "nvidia",
    "research paper", "startup", "launch", "release", "breakthrough",
    # Business / Finance terms (new — additive)
    "business", "finance", "market", "stock", "earnings", "startup funding",
    "crypto", "bitcoin", "economy", "inflation", "layoff", "acquisition",
    "ipo", "revenue", "fed", "interest rate",
]

# Source-to-category mapping for grouping in picker and logging
SOURCE_CATEGORIES = {
    # AI sources
    "OpenAI Blog": "ai",
    "Google AI Blog": "ai",
    "TechCrunch AI": "ai",
    "The Verge AI": "ai",
    "Ars Technica": "ai",
    "MIT Tech Review": "ai",
    "VentureBeat AI": "ai",
    "arXiv cs.AI": "ai",
    "Google News AI": "ai",
    "Reddit r/programming": "ai",
    # Business sources
    "Google News Business": "business",
    "CNBC Business": "business",
    "TechCrunch Startups": "business",
    "MarketWatch": "business",
    # Science sources
    "Google News Science": "science",
    # YouTube channels
    "YouTube CNBC": "business",
    "YouTube Bloomberg Tech": "ai",
    "YouTube Yahoo Finance": "business",
    # Hacker News
    "Hacker News": "ai",  # primarily AI/tech discussions
}

# Sources that have native engagement data (HN points, Reddit upvotes/comments,
# YouTube view counts)
ENGAGEMENT_SOURCES = {
    "Hacker News",
    "Reddit r/programming",
    "YouTube CNBC",
    "YouTube Bloomberg Tech",
    "YouTube Yahoo Finance",
}

REQUEST_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; ShortsBot/1.0)"}
REQUEST_TIMEOUT = 15

# Reddit RSS feeds need a browser UA to avoid 429, and delay between requests
REDDIT_RSS_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
}
REDDIT_RSS_DELAY = 8.0  # seconds between Reddit RSS requests to avoid 429
REDDIT_RSS_MAX_RETRIES = 3


# ---------------------------------------------------------------------------
# 2. Fetching
# ---------------------------------------------------------------------------

def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", " ", text or "").strip()


# Google News RSS redirect resolution
GOOGLE_NEWS_DELAY = 1.5  # seconds between category queries
GOOGLE_NEWS_SOURCES = {"Google News AI", "Google News Business", "Google News Science"}

def _resolve_google_news_redirect(url: str) -> str:
    """Resolve Google News RSS redirect URL to final article URL.

    Google News RSS entries contain redirect URLs (news.google.com/rss/articles/...).
    We follow the redirect to get the real publisher URL.
    """
    try:
        resp = requests.get(url, allow_redirects=True, timeout=10, headers=REQUEST_HEADERS)
        if resp.status_code == 200 and resp.url != url:
            return resp.url
    except Exception:
        pass
    return url  # fallback to original if resolution fails


def _parse_date(raw: str):
    if not raw:
        return None
    try:
        return parsedate_to_datetime(raw)
    except Exception:
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(raw, fmt)
            return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
        except Exception:
            continue
    return None


def fetch_rss(source_name: str, url: str) -> list[dict]:
    """Fetch and parse a single RSS/Atom feed. Returns a list of story dicts."""
    stories = []
    # Reddit RSS feeds need browser UA to avoid 429, and retry
    headers = REDDIT_RSS_HEADERS if "old.reddit.com" in url else REQUEST_HEADERS
    max_retries = REDDIT_RSS_MAX_RETRIES if "old.reddit.com" in url else 0

    for attempt in range(max_retries + 1):
        try:
            resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            root = ET.fromstring(resp.content)
            break
        except Exception as e:
            if attempt == max_retries:
                print(f"  [skip] {source_name}: {e}")
                return stories
            # Retry after delay
            wait = (attempt + 1) * 2
            print(f"  [retry {attempt+1}/{max_retries}] {source_name}: waiting {wait}s... ({e})")
            time.sleep(wait)

    # RSS 2.0: <rss><channel><item>...
    items = root.findall(".//item")
    is_atom = False
    if not items:
        # Atom: <feed><entry>...
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        items = root.findall("atom:entry", ns)
        is_atom = True

    for item in items:
        if is_atom:
            ns = {"atom": "http://www.w3.org/2005/Atom"}
            title = item.findtext("atom:title", default="", namespaces=ns)
            link_el = item.find("atom:link", ns)
            link = link_el.get("href") if link_el is not None else ""
            summary = item.findtext("atom:summary", default="", namespaces=ns) or \
                      item.findtext("atom:content", default="", namespaces=ns)
            published = item.findtext("atom:published", default="", namespaces=ns) or \
                        item.findtext("atom:updated", default="", namespaces=ns)
        else:
            title = item.findtext("title", default="")
            link = item.findtext("link", default="")
            summary = item.findtext("description", default="")
            published = item.findtext("pubDate", default="")

        category = SOURCE_CATEGORIES.get(source_name, "general")

        stories.append({
            "source": source_name,
            "title": (title or "").strip(),
            "link": (link or "").strip(),
            "summary": _strip_html(summary)[:400],
            "published_raw": published,
            "published": _parse_date(published),
            "category": category,
        })

    return stories


def fetch_youtube_feed(source_name: str, channel_id: str) -> list[dict]:
    """Fetch one YouTube channel's Atom video feed.

    Dedicated parser (deliberately NOT folded into fetch_rss): fetch_rss
    discards the XML tree after extracting bare Atom fields, and YouTube
    entries carry the two things we actually want in media:* extensions:
      - media:group/media:description   -> summary (the video description;
        YouTube entries have NO <summary>/<content>, so without this every
        story would get summary="" -> weaker niche scoring AND an empty
        article_fetcher fallback)
      - media:group/media:statistics@views -> story["views"] (real engagement
        signal for score_story)
    Reuses REQUEST_HEADERS/REQUEST_TIMEOUT; _parse_date already handles
    YouTube's ISO-8601 published timestamps. A dead/wrong channel ID fails
    soft: "[skip]" log, empty list.
    """
    stories = []
    url = YOUTUBE_FEED_URL.format(channel_id)
    try:
        resp = requests.get(url, headers=REQUEST_HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
    except Exception as e:
        print(f"  [skip] {source_name}: {e}")
        return stories

    ns = {
        "atom": "http://www.w3.org/2005/Atom",
        "media": "http://search.yahoo.com/mrss/",
    }
    for entry in root.findall("atom:entry", ns):
        title = entry.findtext("atom:title", default="", namespaces=ns)
        link_el = entry.find("atom:link", ns)
        link = link_el.get("href") if link_el is not None else ""
        published = entry.findtext("atom:published", default="", namespaces=ns)

        summary = ""
        views = None
        media_group = entry.find("media:group", ns)
        if media_group is not None:
            summary = (media_group.findtext("media:description",
                                            default="", namespaces=ns) or "").strip()
            stats = media_group.find("media:statistics", ns)
            if stats is not None:
                try:
                    views = int(stats.get("views", ""))
                except (TypeError, ValueError):
                    views = None

        category = SOURCE_CATEGORIES.get(source_name, "general")
        story = {
            "source": source_name,
            "title": (title or "").strip(),
            "link": (link or "").strip(),
            "summary": summary[:400],  # same cap as every other source
            "published_raw": published,
            "published": _parse_date(published),
            "category": category,
        }
        if views is not None:
            story["views"] = views
        stories.append(story)

    return stories


def fetch_youtube_feeds() -> list[dict]:
    """Fetch all configured YouTube channel feeds (see YOUTUBE_CHANNELS)."""
    all_stories = []
    for name, channel_id in YOUTUBE_CHANNELS.items():
        print(f"Fetching {name}...")
        all_stories.extend(fetch_youtube_feed(name, channel_id))
    return all_stories


def fetch_hn_signal(limit: int = 60) -> list[dict]:
    """Pull top Hacker News stories as a 'social engagement' signal source."""
    stories = []
    try:
        ids = requests.get(HN_TOP_STORIES_URL, timeout=REQUEST_TIMEOUT).json()[:limit]
    except Exception as e:
        print(f"  [skip] Hacker News: {e}")
        return stories

    for story_id in ids:
        try:
            item = requests.get(HN_ITEM_URL.format(story_id), timeout=REQUEST_TIMEOUT).json()
        except Exception:
            continue
        if not item or "title" not in item:
            continue
        title = item.get("title", "")
        if not HN_KEYWORDS.search(title):
            continue  # only keep HN stories relevant to the niche
        stories.append({
            "source": "Hacker News",
            "title": title,
            "link": item.get("url", f"https://news.ycombinator.com/item?id={story_id}"),
            "summary": item.get("text", "") or "",  # text-post body (Show/Ask HN)
            "published_raw": "",
            "published": datetime.fromtimestamp(item.get("time", time.time()), tz=timezone.utc),
            "hn_points": item.get("score", 0),
            "hn_comments": item.get("descendants", 0),
            "hn_id": int(story_id),   # so article_fetcher can hit Algolia comments
            "category": "ai",  # HN is primarily AI/tech discussions
        })
    return stories


def collect_all_stories() -> list[dict]:
    all_stories = []
    google_news_seen = 0

    # Group sources by type for interleaved fetching
    reddit_sources = [(name, url) for name, url in RSS_FEEDS.items() if "old.reddit.com" in url]
    google_news_sources = [(name, url) for name, url in RSS_FEEDS.items() if name in GOOGLE_NEWS_SOURCES]
    other_sources = [(name, url) for name, url in RSS_FEEDS.items()
                     if "old.reddit.com" not in url and name not in GOOGLE_NEWS_SOURCES]

    # Interleave: 1 Reddit → 2-3 other → 1 Reddit → 2-3 other → etc.
    # This spaces out Reddit requests naturally without adding extra total time
    reddit_idx = 0
    other_idx = 0

    # Process other sources first (some), then start interleaving
    # First batch: 3 non-Reddit sources
    for _ in range(min(3, len(other_sources))):
        name, url = other_sources[other_idx]
        other_idx += 1
        print(f"Fetching {name}...")
        stories = fetch_rss(name, url)
        all_stories.extend(stories)
        # Add delay between Google News RSS queries
        if name in GOOGLE_NEWS_SOURCES:
            google_news_seen += 1
            if google_news_seen < 3:
                time.sleep(GOOGLE_NEWS_DELAY)

    # Now interleave: Reddit + 2-3 other sources
    while reddit_idx < len(reddit_sources) or other_idx < len(other_sources):
        # Fetch one Reddit source
        if reddit_idx < len(reddit_sources):
            name, url = reddit_sources[reddit_idx]
            reddit_idx += 1
            print(f"Fetching {name}...")
            stories = fetch_rss(name, url)
            all_stories.extend(stories)
            # Reddit RSS already has 8s delay inside fetch_rss on retry

        # Fetch 2-3 other sources
        for _ in range(min(3, len(other_sources) - other_idx)):
            if other_idx >= len(other_sources):
                break
            name, url = other_sources[other_idx]
            other_idx += 1
            print(f"Fetching {name}...")
            stories = fetch_rss(name, url)
            all_stories.extend(stories)
            if name in GOOGLE_NEWS_SOURCES:
                google_news_seen += 1
                if google_news_seen < 3:
                    time.sleep(GOOGLE_NEWS_DELAY)

    print("Fetching Hacker News signal...")
    all_stories.extend(fetch_hn_signal())

    print("Fetching YouTube channel feeds...")
    all_stories.extend(fetch_youtube_feeds())

    return all_stories


# ---------------------------------------------------------------------------
# 3. Ranking
# ---------------------------------------------------------------------------

def score_story(story: dict, now: datetime) -> float:
    score = 0.0

    # Recency: exponential decay over ~48 hours, floor at 0 (soft weight, not hard cutoff)
    if story.get("published"):
        pub = story["published"]
        if pub.tzinfo is None:
            pub = pub.replace(tzinfo=timezone.utc)
        age_hours = max(0.0, (now - pub).total_seconds() / 3600)
        # Exponential decay: score = 40 * e^(-age_hours / 24)
        # ~40 pts at 0h, ~20 pts at 24h, ~10 pts at 48h, ~5 pts at 72h
        import math
        score += 40 * math.exp(-age_hours / 24)
    else:
        score += 10  # unknown date — small default so it isn't discarded

    # Niche relevance: keyword hits in title + summary
    text = f"{story.get('title','')} {story.get('summary','')}".lower()
    hits = sum(1 for kw in NICHE_KEYWORDS if kw in text)
    score += min(hits, 6) * 5  # up to 30 pts

    # Social engagement proxy — normalized per source type
    # Sources with native engagement data (HN, Reddit, YouTube) get engagement score
    # Sources without (plain RSS, Google News) get neutral default (15 pts mid-range)
    if story.get("source") in ENGAGEMENT_SOURCES:
        # Hacker News
        if "hn_points" in story:
            score += min(story["hn_points"] / 10, 20)  # up to 20 pts
            score += min(story.get("hn_comments", 0) / 5, 10)  # up to 10 pts
        # YouTube — real view counts from media:statistics.
        # Linear like the HN formula: 60k views = full 30 pts. Tunable divisor.
        # A fresh video still wins on recency; a day-old viral clip now
        # correctly outranks an ignored one.
        elif "views" in story:
            score += min(story["views"] / 2000, 30)  # up to 30 pts
        # Reddit RSS — engagement in summary field as "X points · Y comments"
        # (Reddit RSS includes this in description; fallback to neutral if missing)
        elif "Reddit" in story.get("source", ""):
            score += 15  # neutral default for Reddit sources
    else:
        # No native engagement data (plain RSS blogs, Google News, etc.)
        # Give neutral default so they aren't structurally penalized
        score += 15  # mid-range engagement default

    return round(score, 2)


def dedupe(stories: list[dict]) -> list[dict]:
    seen_titles = set()
    unique = []
    for s in stories:
        key = re.sub(r"\W+", "", s["title"].lower())[:60]
        if key and key not in seen_titles:
            seen_titles.add(key)
            unique.append(s)
    return unique


def rank_top_stories(candidate_pool: int = 40) -> list[dict]:
    """Heuristic-score and rank all collected stories; return the FULL pool.

    The heuristic layer is the RECALL filter (recency + niche + engagement);
    callers (run_pipeline -> llm_ranker) truncate after the LLM editorial
    rerank, so no top_n slicing happens here anymore.
    """
    now = datetime.now(timezone.utc)
    all_stories = dedupe(collect_all_stories())
    for s in all_stories:
        s["score"] = score_story(s, now)

    all_stories.sort(key=lambda s: s["score"], reverse=True)
    candidates = all_stories[:candidate_pool]

    # Resolve Google News redirects ONLY for top candidates (to save requests)
    print("Resolving Google News redirects for top candidates...")
    for s in candidates:
        if s.get("source") in GOOGLE_NEWS_SOURCES and s.get("link"):
            resolved = _resolve_google_news_redirect(s["link"])
            if resolved != s["link"]:
                s["link"] = resolved
            time.sleep(0.5)  # small delay between redirect resolutions

    # Log summary stats
    cat_counts = defaultdict(int)
    cat_scores = defaultdict(list)
    src_fetch = defaultdict(lambda: {"success": 0, "fail": 0})
    for s in all_stories:
        cat = s.get("category", "general")
        cat_counts[cat] += 1
        cat_scores[cat].append(s["score"])
        src_fetch[s["source"]]["success"] += 1

    print(f"\nCollected {len(all_stories)} unique stories, {len(candidates)} in candidate pool.")
    print("Category breakdown:")
    for cat in ["ai", "business", "science", "general"]:
        if cat_counts[cat]:
            avg = sum(cat_scores[cat]) / len(cat_scores[cat])
            print(f"  {cat}: {cat_counts[cat]} stories, avg score {avg:.1f}")
    print("Source fetch summary:")
    for src, counts in src_fetch.items():
        print(f"  {src}: {counts['success']} fetched")

    # Return the FULL pool — the caller truncates after the LLM rerank.
    return candidates


if __name__ == "__main__":
    top = rank_top_stories()
    print(f"\n=== TOP {len(top)} STORIES ===")
    for i, s in enumerate(top, 1):
        print(f"{i}. [{s['score']}] ({s['source']}) {s['title']}")
