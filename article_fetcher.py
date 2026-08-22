"""
article_fetcher.py
Step 1.5 of the Shorts pipeline: fetch the REAL article text (+ community
comments) for a candidate story, so Groq gets real detail instead of the
≤400-char RSS blurb.

For each story it returns:
  - article_text  : up to ~4000 chars of extracted body ("" if unavailable)
  - comments      : top community comments, text only (HN/Reddit), ≤10
  - source_kind   : "hn" | "reddit" | "rss" | "youtube"
  - used_fallback : True if we fell back to the RSS summary (logged so the
                    fallback rate is visible — see the upgrade spec)
  - fallback_reason: short string when used_fallback is True

Extraction priority: trafilatura (best) → readability-lxml → stdlib
regex HTML-stripper. The stdlib path means a missing dep never hard-fails the
pipeline; it just gives rougher text.

No secrets required. HN uses the public Algolia API; Reddit uses public .json
endpoints (browser UA required — Reddit 403s/429s default python-requests UAs).

Usage (smoke test):
    python article_fetcher.py https://example.com/some-article
"""

import json
import re
import time
import urllib.parse
from datetime import datetime, timezone

import requests

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# A browser-y UA is required for Reddit (it 429s/403s the default python-requests
# UA) and polite for everyone else. Pexels/Cloudflare taught us this lesson once
# already — don't repeat it for the other free endpoints.
BROWSER_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

REQUEST_TIMEOUT = 15
REQUEST_DELAY = 0.6            # be gentle to free public endpoints
MAX_ARTICLE_CHARS = 4000
MAX_FETCH_BYTES = 2 * 1024 * 1024   # don't pull a wholesitemap-sized page
MAX_COMMENTS = 10
HN_ALGOLIA_ITEM = "https://hn.algolia.com/api/v1/items/{id}"
REDDIT_COMMENTS = "https://www.reddit.com/comments/{id}.json"
REDDIT_HEADERS = {"User-Agent": BROWSER_UA, "Accept": "application/json"}

# Comment cache
try:
    from config import COMMENT_CACHE_FILE, COMMENT_CACHE_TTL_DAYS, REDDIT_USER_AGENTS
except ImportError:
    # Fallback defaults if config.py is missing or incomplete
    from pathlib import Path
    COMMENT_CACHE_FILE = Path("cache/comments.json")
    COMMENT_CACHE_TTL_DAYS = 7
    REDDIT_USER_AGENTS = [BROWSER_UA]
import random


# ---------------------------------------------------------------------------
# Extraction backends
# ---------------------------------------------------------------------------

def _extract_article_text(html: str, url: str = "") -> str:
    """Extract clean article text from raw HTML.

    trafilatura (best) → readability-lxml → stdlib regex fallback. Never raises;
    returns "" if every backend fails.
    """
    if not html:
        return ""

    # 1) trafilatura — best main-content extractor, handles boilerplate removal
    try:
        import trafilatura
        text = trafilatura.extract(
            html, url=url or None, include_comments=False,
            include_tables=False, favor_precision=True,
        )
        if text and text.strip():
            return text.strip()
    except Exception:
        pass

    # 2) readability-lxml — Mozilla's Readability port
    try:
        from readability import Document
        doc = Document(html, url=url or None)
        # readability gives summary() (cleaned) but it can still carry tags;
        # strip them to plain text.
        summary_html = doc.summary(html_partial=True)
        text = _strip_html(summary_html)
        if text and len(text) > 80:
            return text.strip()
    except Exception:
        pass

    # 3) stdlib fallback — naive but dependency-free and always available
    text = _strip_html(html)
    return text.strip()


def _strip_html(html: str) -> str:
    """Crude HTML→text: drop tags/scripts, collapse whitespace."""
    if not html:
        return ""
    # remove script/style blocks entirely
    html = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", html)
    # drop remaining tags
    text = re.sub(r"<[^>]+>", " ", html)
    # unescape a few common entities (stdlib html.unescape is fine too)
    import html as htmllib
    text = htmllib.unescape(text)
    # collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _load_comment_cache() -> dict:
    """Load the comment cache from disk."""
    try:
        if COMMENT_CACHE_FILE.exists():
            content = COMMENT_CACHE_FILE.read_text(encoding="utf-8")
            if content.strip():
                return json.loads(content)
    except Exception:
        pass
    return {}


def _save_comment_cache(cache: dict):
    """Save the comment cache to disk."""
    try:
        COMMENT_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        COMMENT_CACHE_FILE.write_text(json.dumps(cache, indent=2), encoding="utf-8")
    except Exception:
        pass


def _get_cached_comments(cache_key: str, cache: dict) -> list[str] | None:
    """Get cached comments if they exist and aren't expired."""
    from datetime import datetime, timedelta, timezone
    entry = cache.get(cache_key)
    if not entry:
        return None
    try:
        cached_at = datetime.fromisoformat(entry.get("cached_at", "").replace("Z", "+00:00"))
        if datetime.now(timezone.utc) - cached_at > timedelta(days=COMMENT_CACHE_TTL_DAYS):
            return None
        return entry.get("comments", [])
    except Exception:
        return None


def _cache_comments(cache_key: str, comments: list[str], cache: dict):
    """Store comments in cache with timestamp."""
    from datetime import datetime, timezone
    cache[cache_key] = {
        "comments": comments,
        "cached_at": datetime.now(timezone.utc).isoformat(),
    }
    _save_comment_cache(cache)


# ---------------------------------------------------------------------------
# HTTP fetch
# ---------------------------------------------------------------------------

# Known paywalled domains that typically block automated fetching
PAYWALLED_DOMAINS = {
    "marketwatch.com", "wsj.com", "bloomberg.com", "ft.com",
    "nytimes.com", "washingtonpost.com", "theinformation.com",
    "businessinsider.com", "barrons.com", "economist.com",
}

# Textise dot iitty endpoints for paywalled content extraction
TEXTISE_ENDPOINTS = [
    "https://r.jina.ai/http://",  # Jina AI reader (free, no key)
    "https://r.jina.ai/https://",
]

def _is_paywalled(url: str) -> bool:
    """Check if URL is from a known paywalled domain."""
    try:
        from urllib.parse import urlparse
        domain = urlparse(url).netloc.lower().replace("www.", "")
        return any(pw in domain for pw in PAYWALLED_DOMAINS)
    except Exception:
        return False


def _fetch_via_textise(url: str) -> str | None:
    """Try to fetch article content via textise services (Jina AI reader)."""
    for endpoint in TEXTISE_ENDPOINTS:
        try:
            textise_url = endpoint + url
            resp = requests.get(textise_url, headers={"User-Agent": BROWSER_UA}, timeout=REQUEST_TIMEOUT)
            if resp.status_code == 200 and resp.text.strip():
                # Jina AI returns clean text
                text = resp.text.strip()
                if len(text) > 200:  # meaningful content
                    return text
        except Exception:
            continue
    return None


def _fetch_url(url: str) -> str | None:
    """Fetch a URL's body as text, size-guarded. Returns None on any failure."""
    if not url or not url.startswith("http"):
        return None
    
    # Skip known paywalled domains early to avoid wasted requests
    if _is_paywalled(url):
        return None
        
    try:
        with requests.get(url, headers={"User-Agent": BROWSER_UA},
                          timeout=REQUEST_TIMEOUT, stream=True,
                          allow_redirects=True) as resp:
            if resp.status_code != 200:
                return None
            # read at most MAX_FETCH_BYTES so a giant page can't OOM us
            chunks = []
            total = 0
            for chunk in resp.iter_content(64 * 1024):
                if not chunk:
                    break
                total += len(chunk)
                chunks.append(chunk)
                if total >= MAX_FETCH_BYTES:
                    break
            # guess encoding, fall back to utf-8
            enc = resp.encoding or "utf-8"
            return b"".join(chunks).decode(enc, errors="replace")
    except Exception:
        return None


def _cap(text: str, limit: int = MAX_ARTICLE_CHARS) -> str:
    if not text:
        return ""
    text = text.strip()
    if len(text) <= limit:
        return text
    # cut at the last sentence boundary within the limit, else hard cut
    cut = text[:limit]
    # Search reversed string for space followed by punctuation (original: punctuation + space)
    m = re.search(r"\s[.!?]", cut[::-1])
    if m:
        end = len(cut) - m.start()
        if end > limit * 0.6:
            return cut[:end].strip()
    return cut.strip()


# ---------------------------------------------------------------------------
# YouTube transcripts
# ---------------------------------------------------------------------------

# Matches watch?v=, youtu.be/, and /shorts/ URLs; captures the 11-char ID.
YT_VIDEO_ID_RE = re.compile(r"(?:watch\?v=|youtu\.be/|shorts/)([A-Za-z0-9_-]{11})")


def _video_id_from_url(url: str) -> str | None:
    """Extract the 11-char video ID from a YouTube watch/youtu.be/shorts URL."""
    m = YT_VIDEO_ID_RE.search(url or "")
    return m.group(1) if m else None


def fetch_youtube_transcript(story: dict) -> str:
    """Fetch a YouTube video's transcript via youtube-transcript-api.

    Returns "" on ANY failure (dep missing, no captions, region-blocked,
    rate-limited) — the caller then falls back to the ≤400-char video
    description exactly like any other failed fetch. Uses the 1.x instance
    API (YouTubeTranscriptApi().fetch()): 0.6.2's module-level
    get_transcript() broke against current YouTube — its timedtext parser
    got empty responses back and died with xml ParseError "no element
    found". to_raw_data() restores the [{text, start, duration}] shape the
    join below expects.
    """
    vid = _video_id_from_url(story.get("link", ""))
    if not vid:
        return ""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        segments = YouTubeTranscriptApi().fetch(vid).to_raw_data()
    except Exception as e:
        # Visible, not swallowed: "dep missing" vs "no captions" vs
        # "IP blocked" are different problems with different fixes — 1.x
        # raises typed errors (TranscriptsDisabled, IpBlocked, ...) so the
        # message names the actual cause.
        print(f"    [yt-transcript] unavailable: {e}")
        return ""
    # Captions come back as timestamped fragments; join + collapse whitespace
    # so the script generator sees one continuous body of text.
    text = " ".join((seg.get("text") or "").strip() for seg in segments)
    return re.sub(r"\s+", " ", text).strip()


# ---------------------------------------------------------------------------
# Comment fetching (HN + Reddit)
# ---------------------------------------------------------------------------

def _hn_id_from_story(story: dict) -> str | None:
    """Find a Hacker News item id for a story (if it's from HN)."""
    if story.get("hn_id"):
        return str(story["hn_id"])
    # fall back to parsing an item?id= link
    link = story.get("link", "") or ""
    m = re.search(r"item\?id=(\d+)", link)
    return m.group(1) if m else None


def fetch_hn_comments(story: dict) -> list[str]:
    """Top HN comments for a story via the public Algolia API.

    HN's real value is the debate in the comments — pull the direct children of
    the story item, author-stripped, text only.
    """
    hn_id = _hn_id_from_story(story)
    if not hn_id:
        return []

    cache_key = f"hn_{hn_id}"
    cache = _load_comment_cache()
    cached = _get_cached_comments(cache_key, cache)
    if cached is not None:
        return cached

    raw = _fetch_json(HN_ALGOLIA_ITEM.format(id=hn_id))
    if not raw or raw.get("type") not in ("story", None):
        return []
    out = []
    for child in raw.get("children") or []:
        if len(out) >= MAX_COMMENTS:
            break
        text = (child or {}).get("text") or ""
        text = _strip_html(text).strip()
        if text:
            out.append(text)

    _cache_comments(cache_key, out, cache)
    return out


def fetch_reddit_comments(story: dict) -> list[str]:
    """Top Reddit comments for a post via the public .json endpoint.

    Requires a browser UA + JSON accept header. Takes the direct top-level
    replies of the original post (op link is at thread[0]; comments at [1]).
    """
    post_id = story.get("post_id")
    if not post_id:
        # fall back to parsing a reddit permalink with an id-like segment
        link = story.get("link", "") or ""
        m = re.search(r"/comments/([a-z0-9]+)", link)
        post_id = m.group(1) if m else None
    if not post_id:
        return []

    cache_key = f"reddit_{post_id}"
    cache = _load_comment_cache()
    cached = _get_cached_comments(cache_key, cache)
    if cached is not None:
        return cached

    # Use rotating UA for Reddit
    ua = random.choice(REDDIT_USER_AGENTS) if REDDIT_USER_AGENTS else BROWSER_UA
    headers = {"User-Agent": ua, "Accept": "application/json"}
    raw = _fetch_json_with_headers(REDDIT_COMMENTS.format(id=post_id), headers)
    if not isinstance(raw, list) or len(raw) < 2:
        return []
    out = []
    comment_listing = raw[1].get("data", {}).get("children", [])
    for c in comment_listing:
        if len(out) >= MAX_COMMENTS:
            break
        body = (c.get("data") or {}).get("body") or ""
        body = body.strip()
        if body and body not in ("[deleted]", "[removed]"):
            out.append(body)

    _cache_comments(cache_key, out, cache)
    return out


def _fetch_json(url: str):
    try:
        resp = requests.get(url, headers={"User-Agent": BROWSER_UA}, timeout=REQUEST_TIMEOUT)
        if resp.status_code != 200:
            return None
        return resp.json()
    except Exception:
        return None


def _fetch_json_with_headers(url: str, headers: dict):
    try:
        resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        if resp.status_code != 200:
            return None
        return resp.json()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Top-level
# ---------------------------------------------------------------------------

def fetch_article_content(story: dict) -> dict:
    """Fetch real article text + comments for a story.

    Returns:
        {
          "article_text": str,        # ≤4000 chars body, "" if unavailable
          "comments":    list[str],   # ≤10 top community comments
          "source_kind":  "hn"|"reddit"|"rss"|"youtube",
          "used_fallback": bool,       # True if we fell back to RSS summary
          "fallback_reason": str,
          "article_chars": int,
          "comment_count": int,
          "fetched_at":   str,        # ISO timestamp
        }

    The summary is short on purpose so it serializes cleanly into metadata.txt
    and lets the caller track fetch fallback rates.
    """
    link = (story.get("link") or "").strip()
    source = (story.get("source") or "").lower()
    fetched_at = datetime.now(timezone.utc).isoformat()

    if "hacker news" in source:
        source_kind = "hn"
    elif "reddit" in source:
        source_kind = "reddit"
    elif source.startswith("youtube"):
        source_kind = "youtube"
    else:
        source_kind = "rss"

    # Route the ARTICLE BODY by URL, not by source string: channel-feed
    # stories carry source="YouTube …", but an HN/Reddit/RSS story whose
    # link IS a video must get the transcript too (watch-page scraping only
    # ever yields footer junk). source_kind stays honest so HN/Reddit
    # stories linking videos keep their comment path.
    is_yt_video = _video_id_from_url(link) is not None

    article_text, comments = "", []
    used_fallback, fallback_reason = False, ""

    # 1) Fetch the article body (works for RSS blogs + external HN links).
    if link:
        # YouTube videos: pull the transcript instead of scraping the watch
        # page (extraction always failed there → every YouTube story used to
        # fall back to the thin ≤400-char video description).
        if is_yt_video:
            transcript = fetch_youtube_transcript(story)
            if transcript:
                article_text = _cap(transcript)
            else:
                fallback_reason = "no transcript available (captions disabled?)"
        # Check for known paywalled domains next
        elif _is_paywalled(link):
            # Try textise service for paywalled content
            textise_content = _fetch_via_textise(link)
            if textise_content:
                article_text = _cap(textise_content)
                fallback_reason = ""
            else:
                fallback_reason = f"known paywalled domain ({link}) - textise failed"
                article_text = ""
        else:
            html = _fetch_url(link)
            if html:
                article_text = _cap(_extract_article_text(html, url=link))
            else:
                fallback_reason = "article fetch failed (timeout/403/empty)"
        time.sleep(REQUEST_DELAY)
    else:
        fallback_reason = "no link"

    # 2) Fetch community comments for the conversational sources.
    try:
        if source_kind == "hn":
            comments = fetch_hn_comments(story)
            time.sleep(REQUEST_DELAY)
        elif source_kind == "reddit":
            comments = fetch_reddit_comments(story)
            time.sleep(REQUEST_DELAY)
    except Exception:
        comments = []   # never let comment failure kill the story

    # 3) Fallback decision: if we got no article AND no comments, use the RSS
    #    summary the fetcher already has, so Groq still gets *something*.
    if not article_text and not comments:
        used_fallback = True
        article_text = (story.get("summary") or "").strip()
        if not fallback_reason:
            fallback_reason = "no article + no comments → RSS summary"

    # User-visible log line (the spec asks us to make the fallback rate visible).
    if used_fallback:
        print(f"    ✗ content fetch fell back to RSS blurb "
              f"({fallback_reason})")
    else:
        print(f"    ✓ fetched {len(article_text)} chars article"
              + (f" + {len(comments)} comments" if comments else ""))

    return {
        "article_text": article_text,
        "comments": comments,
        "source_kind": source_kind,
        "used_fallback": used_fallback,
        "fallback_reason": fallback_reason,
        "article_chars": len(article_text),
        "comment_count": len(comments),
        "fetched_at": fetched_at,
    }


def build_groq_source_block(story: dict, content: dict) -> str:
    """Format the fetched content as the SOURCE MATERIAL block for Groq."""
    parts = [
        f"Title: {story.get('title','')}",
        f"Source: {story.get('source','')}",
    ]
    body = (content.get("article_text") or "").strip()
    parts.append(f"Article body: {body if body else '(no article text available)'}")
    comments = content.get("comments") or []
    if comments:
        cblock = "\n".join(f"- {c}" for c in comments)
        parts.append(f"Community discussion (comments, if available):\n{cblock}")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    url = sys.argv[1] if len(sys.argv) > 1 else None
    if url:
        # Label YouTube URLs like a real pipeline story would be ("YouTube …"
        # → source_kind "youtube"); everything else stays "rss".
        src = "YouTube (direct)" if _video_id_from_url(url) else "rss"
        story = {"title": "(direct url)", "source": src, "link": url}
    else:
        # demo with a recent top HN story
        try:
            top = requests.get(
                "https://hacker-news.firebaseio.com/v0/topstories.json",
                timeout=10).json()[:1]
            item = requests.get(
                f"https://hacker-news.firebaseio.com/v0/item/{top[0]}.json",
                timeout=10).json()
            story = {
                "title": item.get("title", ""),
                "source": "Hacker News",
                "link": item.get("url") or
                        f"https://news.ycombinator.com/item?id={item['id']}",
                "hn_id": item["id"],
                "summary": "",
            }
        except Exception as e:
            print(f"failed to load demo HN story: {e}")
            sys.exit(1)

    print(f"Story: {story['title']}")
    print(f"Link:  {story.get('link')}")
    result = fetch_article_content(story)
    print(json.dumps({k: v for k, v in result.items()
                      if k not in ("article_text", "comments")}, indent=2))
    print("\n--- article_text (first 600 chars) ---")
    print(result["article_text"][:600])
    print("\n--- comments ---")
    for c in result["comments"]:
        print(f"  • {c[:160]}")
