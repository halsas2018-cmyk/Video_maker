"""
llm_ranker.py
Step 1.5 of the Shorts pipeline: LLM editorial rerank of the candidate pool.

The heuristic layer (news_fetcher.score_story) is the cheap RECALL filter: it
cuts ~1500 raw stories down to a ~40-candidate pool using recency + engagement
+ keyword hits. This module is the PRECISION layer: ONE LLM call that reads the
pool and returns an editorially-ranked best-first shortlist with a one-line
reason per pick — the judgment keyword scoring can't make (it's why an arXiv
paper about BPD once topped the AI category on keyword hits alone).

Design constraints:
  - ONE call per run (~7.5k tokens worst case: ~2.5k input + 5120 output cap)
    — fits the Groq free tier alongside the combined script call and tag
    verification.
  - Engagement numbers (HN points, YouTube views) are passed in as RAW
    features; the LLM weighs them itself instead of a hardcoded divisor.
  - The LLM may ONLY pick from the provided numbered IDs — hallucinated or
    repeated IDs are dropped at parse time.
  - ANY failure (bad JSON, <3 valid picks, API error after call_llm's own
    retries) falls back to the input order. Discovery must never hard-depend
    on a live API.

Usage:
    python llm_ranker.py                  # live pool, print both rankings
    python llm_ranker.py --model groq-gpt-oss-20b
"""

import json
import re
import time
from datetime import datetime, timezone

import llm_client

# Pool size the rerank stage expects (news_fetcher returns this many).
POOL_SIZE = 40
# How many picks to ask for when the caller doesn't specify.
DEFAULT_RETURN = 12
# Below this many candidates the LLM call isn't worth the latency.
MIN_POOL = 5

RANK_SYSTEM_PROMPT = """You are the editor of a YouTube Shorts channel covering
tech, AI, and business news for a GENERAL audience (people who don't work in
tech). You are given a numbered list of candidate stories, each with source,
age, engagement data, title, and summary.

Rank the BEST stories for a short-form vertical video. Judge each story on:
- General-audience appeal: would a non-technical viewer stop scrolling?
- Hook potential: a concrete curiosity gap (real number, name, company,
  product, or surprising fact)?
- Freshness: is this actual news, or evergreen/academic filler?
- Visual filmability: could stock footage plausibly illustrate it?

DEMOTE or skip: academic papers without a news hook, niche-insider
discussions, vague headlines with no concrete subject, opinion pieces,
and duplicate coverage of the same event (keep only the best source).

STRICT RULES:
- Pick ONLY from the provided numbered IDs. Never invent IDs.
- Return STRICT JSON only — no markdown fences, no preamble:
{
  "picks": [
    {"id": 3, "reason": "one line, max 120 chars, why this story"},
    ...
  ],
  "duplicate_groups": [[7, 19]]
}
- "picks" is best-first, at most {MAX_PICKS} entries.
- "duplicate_groups" (optional) lists groups of IDs covering the SAME event.
"""


def _pack_story(idx: int, story: dict) -> str:
    """One compact block per story — token discipline (~55 tokens/story)."""
    title = (story.get("title") or "").strip()[:110]
    summary = re.sub(r"\s+", " ", (story.get("summary") or "").strip())[:200]
    source = story.get("source", "?")
    cat = story.get("category", "general")

    # Engagement rendered natively per source type (raw numbers — the LLM
    # weighs them; no hardcoded divisor like the old score_story formula).
    if "hn_points" in story:
        engagement = (f"HN {story.get('hn_points', 0)} points / "
                      f"{story.get('hn_comments', 0)} comments")
    elif "views" in story:
        engagement = f"{story['views']:,} YouTube views"
    else:
        engagement = "no engagement data"

    age = "unknown age"
    pub = story.get("published")
    if pub is not None:
        try:
            if pub.tzinfo is None:
                pub = pub.replace(tzinfo=timezone.utc)
            hours = max(0.0, (datetime.now(timezone.utc) - pub).total_seconds() / 3600)
            age = f"{hours:.0f}h old"
        except Exception:
            pass

    return (f"[{idx}] ({cat}) {source} | {age} | {engagement}\n"
            f"    TITLE: {title}\n"
            f"    SUMMARY: {summary or '(none)'}")


def _parse_picks(raw: str, pool_size: int,
                 max_picks: int) -> tuple[list[dict], list[list[int]]] | None:
    """Parse + validate the LLM JSON. Returns (picks, duplicate_groups) or None.

    picks: [{"id": int, "reason": str}] best-first, deduped, capped.
    Unknown IDs and in-list repeats are dropped; <3 valid picks = failure.
    """
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
    if not isinstance(obj, dict) or not isinstance(obj.get("picks"), list):
        return None

    picks, seen = [], set()
    for p in obj["picks"]:
        if not isinstance(p, dict):
            continue
        try:
            pid = int(p.get("id"))
        except (TypeError, ValueError):
            continue
        if not (1 <= pid <= pool_size) or pid in seen:
            continue  # hallucinated or repeated ID — drop
        seen.add(pid)
        picks.append({"id": pid, "reason": str(p.get("reason") or "").strip()[:120]})
        if len(picks) >= max_picks:
            break

    dup_groups = []
    for g in obj.get("duplicate_groups") or []:
        if isinstance(g, list):
            ids = [i for i in g if isinstance(i, int) and 1 <= i <= pool_size]
            if len(ids) > 1:
                dup_groups.append(ids)

    if len(picks) < 3:
        return None  # too few valid picks — treat the call as failed
    return picks, dup_groups


def rerank(stories: list[dict],
           model_key: str = llm_client.DEFAULT_MODEL_KEY,
           max_picks: int = DEFAULT_RETURN) -> tuple[list[dict], str]:
    """Editorial rerank of a candidate pool. Returns (stories, rank_source).

    Success: stories reordered best-first per the LLM; each picked dict gains
    "llm_reason"; same-event duplicates get "llm_dup_of" = the keeper's
    1-based position. rank_source == "llm".
    Failure: input order unchanged, no new fields, rank_source == "heuristic".
    Never raises.
    """
    if len(stories) < MIN_POOL:
        return stories, "heuristic"

    lines = [_pack_story(i + 1, s) for i, s in enumerate(stories)]
    system = RANK_SYSTEM_PROMPT.replace("{MAX_PICKS}", str(max_picks))
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": "Rank these candidates now:\n\n" + "\n".join(lines)},
    ]

    t0 = time.perf_counter()
    try:
        # 5120, not 1024: GPT-OSS spends hidden reasoning from the SAME
        # token budget (known issues 12/20). At 1024 it reasoned about all
        # 40 candidates and returned empty content on every attempt —
        # same failure mode that forced COMBINED_MAX_TOKENS up to 4096.
        raw = llm_client.call_llm(messages, model_key=model_key,
                                  temperature=0.2, max_tokens=5120)
        parsed = _parse_picks(raw, len(stories), max_picks)
    except Exception as e:
        print(f"  [llm-rank] fell back to heuristic order: {e}")
        return stories, "heuristic"

    if parsed is None:
        print("  [llm-rank] fell back to heuristic order: unparseable or too few valid picks")
        return stories, "heuristic"

    picks, dup_groups = parsed
    ordered, by_id = [], {}
    for p in picks:
        s = dict(stories[p["id"] - 1])
        s["llm_reason"] = p["reason"]
        by_id[p["id"]] = s
        ordered.append(s)
    # Stories the LLM didn't pick keep heuristic order after the picks.
    picked_ids = set(by_id)
    ordered.extend(s for i, s in enumerate(stories, 1) if i not in picked_ids)

    # Duplicate groups: keeper = best-ranked member; later members get
    # llm_dup_of so the picker can annotate (interactive) or drop (--auto).
    pick_pos = {p["id"]: pos for pos, p in enumerate(picks, 1)}
    for g in dup_groups:
        members = sorted((i for i in g if i in pick_pos), key=lambda i: pick_pos[i])
        if len(members) < 2:
            continue
        keeper_pos = pick_pos[members[0]]
        for mid in members[1:]:
            by_id[mid]["llm_dup_of"] = keeper_pos
        a = stories[members[0] - 1].get("title", "")[:45]
        b = stories[members[1] - 1].get("title", "")[:45]
        print(f'  [llm-rank] duplicate event: "{a}" == "{b}"')

    print(f"  [llm-rank] {len(picks)} picks in {time.perf_counter() - t0:.1f}s "
          f"(model: {model_key})")
    return ordered, "llm"


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Standalone LLM editorial rerank smoke test.")
    ap.add_argument("--model", default=llm_client.DEFAULT_MODEL_KEY)
    ap.add_argument("--pool", type=int, default=POOL_SIZE,
                    help="candidate pool size to fetch and rank")
    args = ap.parse_args()

    from news_fetcher import rank_top_stories
    pool = rank_top_stories(candidate_pool=args.pool)
    print(f"\nPool: {len(pool)} candidates")

    t0 = time.perf_counter()
    ranked, src = rerank(pool, model_key=args.model)
    print(f"\n=== RANKING ({src}, {time.perf_counter() - t0:.1f}s total) ===")
    for i, s in enumerate(ranked, 1):
        print(f"{i:2d}. [{s.get('score', '?')}] ({s['source']}) {s['title'][:70]}")
        if s.get("llm_reason"):
            print(f"      -> {s['llm_reason']}")
        if s.get("llm_dup_of"):
            print(f"      -> same event as #{s['llm_dup_of']}")
