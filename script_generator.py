"""
script_generator.py
Step 2-3 of the Shorts pipeline: the COMBINED script + headline + per-sentence
visual plan call.

Per pipeline_upgrade_spec.md, this is now ONE Groq call that produces, together:
  - the narration script (6-9 sentences, hook-first, concrete-grounded)
  - 5 headline options + a chosen headline
  - a Pexels search_term + asset_type (video|photo) for EACH sentence

Doing it in one call (instead of script-gen then per-sentence keyword-gen) means
the model sees the FULL script context when picking every keyword — which fixes
the generic/repetitive stock-footage problem the upgrade targets — and cuts a
Groq round-trip.

It is fed REAL article text + top community comments (fetched by
article_fetcher) instead of the old ≤400-char RSS blurb.

Uses Groq free API (fast, reliable, 30 req/min free). Sign up at console.groq.com.
Requires: environment variable GROQ_API_KEY
"""

import os
import re
import json
import time
import subprocess
from pathlib import Path

# Import the new provider-agnostic LLM client
import llm_client

# Auto-load .env for key
_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    for _line in _env_path.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            _k, _v = _k.strip(), _v.strip().strip('"').strip("'")
            if _k == "GROQ_API_KEY" and not os.environ.get(_k):
                os.environ[_k] = _v

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"

# Word targets sized for ~33-45s Shorts at the +20% narration rate
# (~3.9 words/sec → 110-150 words ≈ 28-38s; we keep the +20% rate that's
# tuned for Shorts, accept landing around the lower end of the 30-60s goal).
MAX_WORDS = 150
MIN_WORDS = 110

# The combined call returns script + 5 headlines + a per-sentence plan (each
# shot has sentence/search_term/asset_type). That JSON is bigger than a bare
# script, so bump the token cap so the response isn't truncated mid-array.
# Reasoning models (GPT-OSS) draw their hidden reasoning from this SAME
# budget — 2048 truncated mid-JSON on real runs (finish_reason=length),
# so 4096 leaves room for reasoning + the full JSON payload.
COMBINED_MAX_TOKENS = 4096

# Banned filler loaded from config.py (allows env override)
from config import BANNED_FILLER


def _call_llm(messages: list[dict], temperature: float = 0.5,
              max_tokens: int = 1024,
              model_key: str = llm_client.DEFAULT_MODEL_KEY) -> str:
    """Wrapper over llm_client.call_llm for backward compatibility.

    Uses the specified model_key so the caller can choose the model.
    """
    return llm_client.call_llm(
        messages=messages,
        model_key=model_key,
        temperature=temperature,
        max_tokens=max_tokens,
    )


# ---------------------------------------------------------------------------
# Step 2-3 — Combined script + headline + per-sentence visual plan (ONE call)
# ---------------------------------------------------------------------------

COMBINED_SYSTEM_PROMPT = f"""You are writing a script for a short-form vertical
video (30-45 seconds) about a tech/AI news story, AND selecting stock footage
search terms for each sentence. Your only goal is to make someone stop scrolling
and watch to the end. The video has NO on-screen text — the visuals must carry
the meaning alongside the narration.

Audience: the GENERAL PUBLIC — people who do NOT work in tech, do not know what
an LLM/parameter/benchmark is. Plain words only.

SOURCE MATERIAL (fetched for you — use it; do NOT invent facts):
{{SOURCE}}

PART A — SCRIPT RULES:
1. HOOK (sentence 1, 0-3s): Open with a SPECIFIC, CONCRETE detail from the
   source — a real number, a named person/community/tool, or a surprising fact
   pulled from the article or the comments. Create a curiosity gap. NEVER open
   with "Imagine...", "What if...", "Have you ever...", "Picture this...". A
   vague generality ("a debate is brewing") is a failure.
2. SPECIFICITY: Use real names, real numbers, and quotes/paraphrased opinions
   from the article and comments. Vague paraphrase is a failure state.
3. EMOTIONAL ARC: Vary the tone sentence to sentence — curiosity/stakes,
   tension/conflict, relatable anxiety, then a turn or payoff. Do not stay flat
   and explanatory throughout.
4. HUMAN TOUCH: Write like telling a friend something wild you just read.
   Contractions fine. Mix short punchy sentences with longer ones. No corporate
   hedging, no press-release tone.
5. NO FILLER CLOSERS: NEVER end with "be a part of the conversation", "what do
   you think", or "let me know". End with a real payoff — an outcome, a striking
   implication, or a genuine open question tied to specific stakes.
6. LENGTH: 6-9 sentences total, {MIN_WORDS}-{MAX_WORDS} words. Each sentence is
   a distinct filmable beat. Aim for the middle of the word range.
7. PLAIN NARRATION: clean punctuation for text-to-speech, no jargon, no acronyms
   without explanation, no emojis/symbols/markdown, no brackets or directions.

PART B — HEADLINE + YOUTUBE METADATA RULES:
Generate 5 headline options (on-screen hook). Each must create a curiosity gap
using a CONCRETE noun from the story (a name, tool, number, or group) — not a
generic phrase. Under 8 words. The chosen_headline is the one you'd put on-screen
as the hook text.
Bad: "Coding Just Got Personal"
Good: "This Subreddit Banned ChatGPT Code"

ALSO generate:
- youtube_title: punchy, ≤60 chars, curiosity-gap, YouTube-SEO style (NOT the raw
  source title). Banned: "You won't believe", all-caps shouting, clickbait that
  doesn't match the content.
- youtube_description: 1-2 line short caption (≤200 chars) ending with relevant
  hashtags, suitable to paste into Shorts description box.

PART C — PER-SENTENCE KEYWORD RULES:
For EACH script sentence choose ONE OR MORE Pexels search terms (sub_shots):
- Prefer SPECIFIC, story-relevant terms over generic ones. "programmer using AI
  assistant screen" beats "person coding laptop".
- If the sentence names a concrete subject (tool, platform, action), reference
  it directly in the search term.
- Only fall back to a generic human-emotion shot if the sentence describes a
  feeling with no visual referent — even then, keep it tech/coding-adjacent.
- Do NOT repeat the same search term or visual concept across sentences — you
  can see all sentences at once, so ensure VISUAL VARIETY across the full set.
- Choose "media_type" as "video" by default (stock video clearly fits most tech
  topics). Use "photo" ONLY when the visual is explicitly a static image:
  a specific book cover, a landmark, a distinct object, a person's portrait.
- **IMPORTANT: Prefer search terms that stock sites actually have:**
  'person coding', 'AI abstract', 'futuristic background', 'data visualization',
  'technology background', 'person working laptop', 'server room', 'neural network animation' —
  NOT specific names/titles like "Ray Kurzweil speaking" or "Vernor Vinge book cover"
  (stock sites rarely have these tagged).
- For longer sentences (>4s), provide MULTIPLE sub_shots (2-3) with DIFFERENT
  search terms so the sentence duration is covered by varied assets, not by
  looping the same clip. Each sub_shot covers ~2-3s of narration.

OUTPUT FORMAT (strict JSON, no markdown fences, no preamble):
{{
  "headline_options": ["...", "...", "...", "...", "..."],
  "chosen_headline": "...",
  "youtube_title": "...",
  "youtube_description": "...",
  "shots": [
    {{
      "sentence": "...",
      "sub_shots": [
        {{"search_term": "...", "media_type": "video"|"photo", "visual_description": "..."}},
        {{"search_term": "...", "media_type": "video"|"photo", "visual_description": "..."}}
      ]
    }},
    ...
  ]
}}
The "shots" array length MUST equal your sentence count (6-9).
Each sentence MUST have at least 1 sub_shot.
VIDEO sub_shots: max 3.0 seconds each. PHOTO sub_shots: max 1.5 seconds each.
Aim for total visual coverage matching sentence duration — longer sentences need more sub_shots.
"""


def _source_block(story: dict, content: dict) -> str:
    """Build the SOURCE MATERIAL block from fetched content (hfed into the prompt)."""
    parts = [
        f"Title: {story.get('title','')}",
        f"Source: {story.get('source','')}",
    ]
    body = (content.get("article_text") or "").strip()
    parts.append(f"Article body: {body if body else '(no article text — rely on the title and your judgement)'}")
    comments = content.get("comments") or []
    if comments:
        cblock = "\n".join(f"- {c}" for c in comments)
        parts.append(f"Community discussion (comments):\n{cblock}")
    return "\n".join(parts)


def generate_combined(story: dict, content: dict,
                     retry_feedback: str = "",
                     model_key: str = llm_client.DEFAULT_MODEL_KEY) -> dict:
    """One LLM call → script + headlines + per-sentence plan. Validates + retries once.

    Returns a dict: {script, headline_options, chosen_headline, youtube_title,
    youtube_description, shots, word_count}.
    `shots` is the per-sentence list with sentence/search_term/asset_type, in
    order. `script` is reconstructed by joining shot sentences (so the script
    and the plan are guaranteed to have the same sentence count — this kills a
    whole class of drift between script, captions, and assembly).
    """
    source = _source_block(story, content)
    system = COMBINED_SYSTEM_PROMPT.replace("{SOURCE}", source)
    user_prompt = "Write the script + 5 headlines + per-sentence shots now."
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user_prompt},
    ]
    if retry_feedback:
        messages.append({"role": "assistant", "content": "(previous output rejected)"})
        messages.append({"role": "user", "content": retry_feedback})

    raw = _call_llm(messages, temperature=0.7, max_tokens=COMBINED_MAX_TOKENS, model_key=model_key)
    if not raw or not raw.strip():
        raise RuntimeError(f"LLM returned empty response for model '{model_key}'")
    parsed = _parse_combined(raw)
    if not parsed:
        raise RuntimeError(f"LLM combined output was not valid JSON: {raw[:200]}")

    # Validate — one retry if banned filler or near-duplicate search terms slip in.
    problems = _validate(parsed, story)
    if problems and not retry_feedback:   # retry exactly once
        fb = "Your previous output had these problems; fix them and return the full JSON again:\n- " + "\n- ".join(problems)
        print(f"  [validator] retrying: {len(problems)} issue(s): {problems[0]}")
        return generate_combined(story, content, retry_feedback=fb)

    # Normalize shots to the downstream contract (sub_shots with search_term + media_type).
    shots = []
    for s in parsed.get("shots", []):
        sentence = str(s.get("sentence") or "").strip()
        sub_shots = s.get("sub_shots") or []
        if not sub_shots:
            # fallback: at least one sub_shot per sentence
            sub_shots = [{
                "search_term": str(s.get("search_term") or "technology").strip(),
                "asset_type": str(s.get("asset_type") or "video").lower().strip(),
                "visual_description": sentence
            }]

        normalized_sub_shots = []
        for ss in sub_shots:
            term = str(ss.get("search_term") or "").strip()
            am = str(ss.get("asset_type") or ss.get("media_type") or "").lower().strip()
            vis_desc = str(ss.get("visual_description") or "").strip()
            normalized_sub_shots.append({
                "search_term": term,
                "media_type": "photo" if am == "photo" else "video",
                "visual_description": vis_desc
            })

        shots.append({
            "sentence": sentence,
            "sub_shots": normalized_sub_shots,
        })
    script = " ".join(s["sentence"] for s in shots if s["sentence"]).strip()
    word_count = len(script.split())

    # Calculate script quality metrics
    sentences = [s["sentence"] for s in shots if s["sentence"]]
    all_words = " ".join(sentences).lower().split()
    unique_words = set(all_words)
    lexical_diversity = len(unique_words) / len(all_words) if all_words else 0
    
    # Sentence length variety
    sent_lengths = [len(s.split()) for s in sentences]
    avg_sent_len = sum(sent_lengths) / len(sent_lengths) if sent_lengths else 0
    sent_len_variance = sum((l - avg_sent_len) ** 2 for l in sent_lengths) / len(sent_lengths) if sent_lengths else 0
    
    quality_metrics = {
        "word_count": word_count,
        "sentence_count": len(sentences),
        "lexical_diversity": round(lexical_diversity, 3),
        "avg_sentence_length": round(avg_sent_len, 1),
        "sentence_length_variance": round(sent_len_variance, 1),
        "unique_words": len(unique_words),
    }

    return {
        "script": script,
        "headline_options": parsed.get("headline_options", []),
        "headline": (parsed.get("chosen_headline") or "").strip(),
        "youtube_title": (parsed.get("youtube_title") or "").strip(),
        "youtube_description": (parsed.get("youtube_description") or "").strip(),
        "shots": shots,
        "word_count": word_count,
        "quality_metrics": quality_metrics,
    }


def _parse_combined(raw: str) -> dict | None:
    """Parse the combined JSON, tolerating markdown fences / extra prose."""
    if not raw:
        return None
    txt = raw.strip()
    # strip ```json fences if present
    txt = re.sub(r"^```(?:json)?|```$", "", txt, flags=re.MULTILINE).strip()
    # if there's stray prose around the JSON, grab the first {...} block
    if not txt.startswith("{"):
        m = re.search(r"\{.*\}", txt, flags=re.DOTALL)
        if m:
            txt = m.group(0)
    try:
        obj = json.loads(txt)
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    if "shots" not in obj or not isinstance(obj["shots"], list):
        return None
    return obj


def _normalize_term(t: str) -> str:
    """Normalize a search term for near-duplicate comparison."""
    return re.sub(r"[^a-z0-9]+", " ", (t or "").lower()).strip()


def _validate(parsed: dict, story: dict = None) -> list[str]:
    """Return a list of problems that warrant a single rewrite (empty = OK).

    - any sentence starts with / contains a banned filler phrase
    - two search_term values are near-duplicates (same normalized form) across ALL sub_shots
    - sentence count outside 6-9
    - youtube_title missing, empty, or >60 chars
    - youtube_description missing, empty, or >200 chars
    - headline relevance to source (if story provided)
    """
    problems = []
    shots = parsed.get("shots", [])
    if not (6 <= len(shots) <= 9):
        problems.append(f"sentence count {len(shots)} is outside the 6-9 target")
    seen_terms = {}
    for s in shots:
        sent = (s.get("sentence") or "").strip()
        low = sent.lower()
        for b in BANNED_FILLER:
            if low.startswith(b) or (" " + b) in low or low == b:
                problems.append(f'sentence opens with/contains banned filler "{b}": "{sent[:50]}"')
                break
        # Check all sub_shots for near-duplicate search terms
        sub_shots = s.get("sub_shots", [])
        for ss in sub_shots:
            term = _normalize_term(ss.get("search_term", ""))
            if term and term in seen_terms:
                problems.append(
                    f'near-duplicate search term "{ss.get("search_term")}" '
                    f'(repeats "{seen_terms[term]}") — make shots visually distinct')
            elif term:
                seen_terms[term] = ss.get("search_term", "")
    # headline sanity
    if not parsed.get("chosen_headline"):
        problems.append("missing chosen_headline")
    elif story:
        # Validate headline relevance to source material
        headline = parsed.get("chosen_headline", "").lower()
        source_text = (story.get("title", "") + " " + story.get("summary", "")).lower()
        # Check if headline shares at least one meaningful word with source
        headline_words = set(re.findall(r'\b\w{4,}\b', headline))
        source_words = set(re.findall(r'\b\w{4,}\b', source_text))
        if headline_words and source_words:
            overlap = headline_words & source_words
            if not overlap:
                problems.append(f'headline "{parsed.get("chosen_headline")}" shares no meaningful words with source title/summary')
        
        # Additional: headline must contain at least one specific entity (number, proper noun, name)
        # NOTE: no local `import re` here — it would shadow the module-level
        # import and make every earlier re.* use in this function raise
        # UnboundLocalError ("cannot access local variable 're'").
        has_number = bool(re.search(r'\b\d+[kKmMbB%]?\b', parsed.get("chosen_headline", "")))
        has_proper_noun = bool(re.search(r'\b[A-Z][a-z]+\b', parsed.get("chosen_headline", "")))
        if not (has_number or has_proper_noun):
            problems.append(f'headline "{parsed.get("chosen_headline")}" lacks specific entity (number, name, or proper noun) — make it concrete')
    
    # Validate headline options too (all 5 should be concrete)
    for opt in parsed.get("headline_options", []):
        opt_lower = opt.lower()
        opt_words = set(re.findall(r'\b\w{4,}\b', opt_lower))
        # `source_words` only exists when `story` was provided — guard so a
        # story=None call with a missing chosen_headline can't NameError here.
        if opt_words and story and source_words:
            overlap = opt_words & source_words
            if not overlap:
                problems.append(f'headline option "{opt}" shares no meaningful words with source')
                break  # one is enough to flag
    
    # youtube metadata sanity
    yt_title = (parsed.get("youtube_title") or "").strip()
    if not yt_title:
        problems.append("missing youtube_title")
    elif len(yt_title) > 60:
        problems.append(f"youtube_title exceeds 60 chars ({len(yt_title)})")
    yt_desc = (parsed.get("youtube_description") or "").strip()
    if not yt_desc:
        problems.append("missing youtube_description")
    elif len(yt_desc) > 200:
        problems.append(f"youtube_description exceeds 200 chars ({len(yt_desc)})")
    return problems


def process_story(story: dict, content: dict | None = None,
                  model_key: str = llm_client.DEFAULT_MODEL_KEY) -> dict:
    """End-to-end Step 2-3 for one story: ONE combined LLM call.

    Now fetches real article text + comments (via article_fetcher) and produces
    a script + 5 headline options + chosen headline + per-sentence visual plan
    in a single LLM round-trip, then validates (banned filler, near-duplicate
    search terms) with one retry pass.

    `content` is the dict from article_fetcher.fetch_article_content(). When
    None, it is fetched here (so standalone `python script_generator.py` works).
    """
    # --- Step 1.5: fetch real content (unless the caller already did) ---
    if content is None:
        from article_fetcher import fetch_article_content
        content = fetch_article_content(story)

    print(f"Writing script + headlines + visual plan: {story['title'][:60]}...")
    result = generate_combined(story, content, model_key=model_key)
    script = result["script"]
    print(f"  ✓ script ({result['word_count']} words, "
          f"{len(result['shots'])} shots, headline: \"{result['headline']}\")")

    # Lightweight research_notes.json (kept for downstream / metadata parity).
    # The combined call supersedes the old separate research step — the raw
    # article IS the research now, so we keep a small summary for the artifact.
    research = {
        "source_kind": content.get("source_kind", "rss"),
        "used_fallback": content.get("used_fallback", False),
        "fallback_reason": content.get("fallback_reason", ""),
        "article_chars": content.get("article_chars", 0),
        "comment_count": content.get("comment_count", 0),
        "headline_options": result.get("headline_options", []),
        "fetched_at": content.get("fetched_at", ""),
    }
    result["research"] = research
    result["story"] = story
    return result


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1].startswith("http"):
        # live-fetch a real article and process it
        from article_fetcher import fetch_article_content
        story = {"title": "(from URL)", "source": "cli", "link": sys.argv[1], "summary": ""}
        res = process_story(story)
        print(json.dumps({k: v for k, v in res.items() if k != "story"},
                         indent=2)[:4000])
    else:
        test_story = {
            "title": "Google DeepMind reshuffle — Demis Hassabis moves from CEO to chair",
            "source": "TLDR AI",
            "link": "https://example.com",
            "summary": "Hassabis moves to chair; a new head of Gemini takes the CEO role.",
        }
        result = process_story(test_story)
        out = {k: v for k, v in result.items() if k != "story"}
        print(json.dumps(out, indent=2)[:4000])
