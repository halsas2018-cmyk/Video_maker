# CLAUDE.md — YouTube Shorts AI Agent

Project guide for Claude (and any developer). Captures the architecture, the
end-to-end pipeline, each module's contract, environment setup, known issues,
and reasoning hooks for adding features. Read this before modifying the code.

---

## 1. What this project is

A fully automated, **zero-cost** pipeline that turns trending AI/tech/business news into
ready-to-edit YouTube Shorts (9:16, 1080×1920). It discovers stories from free
RSS feeds + Hacker News + Reddit + **Google News RSS**, writes a non-technical "hook-first" narration script,
generates voiceover, asks LLM for a per-sentence visual plan, downloads one
free stock video/photo PER SENTENCE (so clips align with the words spoken),
and cuts them together chronologically into a draft MP4.

**Visual quality features:**
- **xfade transitions** between clips (fade, wipe, slide, zoomin — varied per cut, 0.2s)
- **Ken-Burns zoompan** on photos (pre-scaled 1.12×, ~30% zoom-out variety)
- **LLM tag verification** — Groq checks Pexels tags/alt before download (YES/NO relevance)
- **Asset reuse limit** — max 2 uses per clip per video; auto re-downloads fresh if exhausted
- **Subtle transition clicks** — 1000Hz sine at sentence boundaries
- **NO burned-in captions** — `captions.srt` is separate reference file

- **Language/Runtime:** Python 3.13 (works on 3.10+)
- **External paid APIs:** NONE. Two free APIs — Groq (LLM: research, script,
  AND per-sentence visual plan + tag verification) and Pexels (stock video + photos).
  Optional: NVIDIA NIM (free tier) for alternative LLM models.
- **Clip duration limits:** Video clips capped at 3.0s, photos at 1.5s (hard max).
  Longer narration sentences split into multiple clips. Each asset reused ≤2 times per video.
- **Transition click sounds:** Subtle 1000Hz sine clicks (0.02s, 15% volume) at sentence boundaries via ffmpeg `-itsoffset`+`amix`.
- **Reddit via RSS:** Uses `old.reddit.com/r/{sub}/top/.rss` feeds (no auth, no IP blocks on normal hosts).
  Browser UA + 8s delay + 3 retries to avoid 429s.
- **Local tools required:** `ffmpeg`, `ffprobe`, `curl` (all called via `subprocess`).
- **Entry point:** `run_pipeline.py`

---

## 2. Environment setup

A `.env` file in the project root holds all secrets and is auto-loaded by
`run_pipeline.py`, `script_generator.py`, and `llm_client.py` (manual `export` not required).

Required keys:
```
GROQ_API_KEY="gsk-..."      # script generation (free, 30 req/min) — console.groq.com
PEXELS_API_KEY="qkn-..."    # stock footage download (free) — pexels.com/api
```

Optional key:
```
NVIDIA_API_KEY="nvapi-..."  # alternative LLM models on NVIDIA NIM (free tier) — build.nvidia.com
```

Python dependencies (`pip install -r requirements.txt`):
- `requests` — RSS + Hacker News fetching (`news_fetcher.py`)
- `edge-tts` — local Microsoft Edge TTS voiceover (`voice_generator.py`)
- `Pillow` — gradient fallback background (`video_assembler.py`)
- stdlib only otherwise: `urllib` (Pexels download), `xml.etree` (RSS parse),
  `subprocess` (ffmpeg + Groq/NVIDIA via curl)

System tools (install separately, NOT via pip):
- `ffmpeg`, `ffprobe` — video assembly and duration probing
- `curl` — LLM API calls (the script uses curl, not the `requests` lib, for LLM)

`.gitignore` already excludes `.env` and `output/` — keep it that way.

---

## 3.5. Story Selection Process

The pipeline selects stories in this order:

1. **Fetch & Rank** (`news_fetcher.rank_top_stories`):
   - Pulls from all RSS feeds (Tech, AI, Business, Reddit RSS, **Google News RSS**) + Hacker News top stories
   - Each story tagged with a **category** (ai / business / science) by source
   - Each story scored on:
     - **Recency** (≤40 pts): exponential decay over ~48 hours (newer = higher; soft weight, not hard cutoff — a 2-day-old story with strong engagement can outrank a fresh one with none)
     - **Niche relevance** (≤30 pts): keyword hits in title+summary against `NICHE_KEYWORDS`
     - **Engagement** (≤30 pts): normalized per source type — HN/Reddit get native points/comments; plain RSS blogs and Google News get a neutral default (15 pts) so they aren't structurally penalized for lacking engagement data
   - Returns top N candidates (default pool: 30, ranked by combined score)
   - **Logging:** prints candidate count and average score per category, and per-source fetch success counts

2. **Daily Dedupe** (`output/_generated_log.json`):
   - Filters out stories already generated today (by normalized title fingerprint)
   - Skipped unless `--no-dedupe` flag used

3. **Interactive Picker** (unless `--auto` or non-TTY):
   - Displays ranked list **grouped by category** (AI / BUSINESS / SCIENCE) with scores and sources — **ALL stories shown**
   - Prompts: `Pick stories to generate (e.g. 1,3,5 / all / top3): `
   - **Commands:**
     - `1,3,5` — specific indices
     - `top3` / `top5` — top N
     - `all` — generate all shown candidates
     - `Enter` (empty) — defaults to top-N (where N = `--count`)

4. **Generation**:
   - For each selected story: fetch article/comments → LLM call → voice → assets → assemble

---

## 3. Pipeline architecture

Run with:
```
python run_pipeline.py --count 3 --outdir output
python run_pipeline.py --quick          # 1 script only, no video (fast review)
python run_pipeline.py --count 1        # one full video incl. assembly
python run_pipeline.py --count 2 --no-video   # scripts + voice only
python run_pipeline.py --model groq-deepseek --count 1  # use DeepSeek on Groq
python run_pipeline.py --model nvidia-nemotron-super --count 1  # use NVIDIA NIM
python run_pipeline.py --model nvidia-gpt-oss-120b --count 1  # use GPT-OSS 120B on NVIDIA
python run_pipeline.py --auto --count 5     # non-interactive (cron-friendly)
python run_pipeline.py --no-dedupe --count 3 # allow regenerating today's stories
python run_pipeline.py --compare-models "groq-llama33,nvidia-nemotron-super" --count 1  # cross-model comparison
python run_pipeline.py --with-hook-text --count 1  # burn headline on gradient fallback
```

Flow (one story → one Short):

```
run_pipeline.py (main)
  │
  ├─ Step 1: news_fetcher.rank_top_stories()
  │     ├─ fetch_rss()      for each feed in RSS_FEEDS (incl. TLDR AI, TLDR Tech/Founders/Crypto, CNBC, TechCrunch Startups, MarketWatch, Reddit RSS, Google News RSS)
  │     ├─ fetch_hn_signal()   Hacker News top stories (niche-filtered: AI + business/finance); keeps
  │     │                     hn_id so comments can be fetched later
  │     ├─ dedupe() + score_story()  (recency + niche + normalized engagement per source type)
  │     └─ returns top N story dicts with category field
  │
  ├─ Daily dedupe: filter out stories already generated today (output/_generated_log.json)
  │
  ├─ Story picker (interactive, unless --auto or non-TTY):
  │     Print ranked candidates grouped by category; prompt "Pick stories to generate (e.g. 1,3,5 / all / top3 / list): "
  │
  └─ for each selected story:
       process_story(story, model_key)            # script_generator.py
         ├─ Step 1.5: article_fetcher.fetch_article_content(story)
         │     ├─ _fetch_url(link) → html (size-guarded, browser UA)
         │     ├─ _extract_article_text(html)  trafilatura → readability → stdlib
         │     ├─ HN comments  (hn.algolia.com/api/v1/items/{id}, top ~10)
         │     │  OR Reddit comments (reddit.com/comments/{id}.json, top ~10)
         │     └─ on any failure: fall back to story['summary'] + LOG it
         │        (source_kind, used_fallback, fallback_reason, chars, comment_count)
         │
         ├─ generate_combined(story, content, model_key)  → LLM (ONE call)
         │     Returns {script, headline_options[5], headline, youtube_title, youtube_description,
         │       shots[], word_count, research}:
         │       - script: 6-9 sentences, 110-150 words (~33-45s at +20% TTS)
         │       - hook: CONCRETE-grounded (real number/name/quote), NOT "Imagine..."
         │       - youtube_title: punchy ≤60 chars, YouTube-SEO style
         │       - youtube_description: ≤200 chars + hashtags
         │       - per-sentence shots: [{sentence, search_term, media_type}]
         │         chosen with full-script context → varied, story-specific terms
         │     ├─ _validate(): ban filler ("imagine"/"what do you think"/etc),
         │     │              reject near-duplicate search_terms, 6-9 sentence count,
         │     │              youtube_title ≤60 chars, youtube_description ≤200 chars
         │     └─ 1 retry pass with feedback if validation fails
         │
       save_project(result, ..., model_key)        # run_pipeline.py
         ├─ write script.txt, research_notes.json, metadata.txt (+ fetch block)
         ├─ write youtube_meta.json (title, description, on-screen hook, source link)
         ├─ voice_generator.generate_narration()  → narration.mp3 (edge-tts)
         ├─ ffprobe narration → real sentence_timings (run_pipeline)
         ├─ storyboard_generator.generate_storyboard(script, title, project_dir,
         │     sentence_timings, plan=result["shots"], headline=result["headline"])
         │     → storyboard.md, captions.srt, asset_plan.json (per_sentence plan
         │       + headline), timing.json, headline.txt, thumbnail_notes.txt
         │     [NO LLM call — uses the plan from generate_combined directly;
         │      generate_visual_plan() is only the fallback when no plan passed]
         ├─ _generate_edit_plan()  (run_pipeline.py)
         ├─ asset_collector.collect_assets_for_plan()  → assets/*.{mp4,jpg}
         │     (one asset PER SENTENCE: video OR photo, Pexels, cached, ≤15MB)
         │     **LLM tag verification before download (YES/NO)**
         └─ video_assembler.assemble_video_simple()  → draft_video.mp4 (ffmpeg)
               (chronological clip-per-sentence cut; HARD MAX: video 3.0s, photo 1.5s;
                longer sentences split into multiple clips; asset reused ≤2×/video;
                **xfade transitions (fade/wipe/slide/zoomin, 0.2s); Ken-Burns zoompan
                on photos with ~30% zoom-out variety; subtle click at sentence boundaries;
                audio apad'd so last words never clip; NO burned captions/text)
               **If assets exhausted (2-use limit), raises error → pipeline re-downloads fresh (max 2 retries)**
```

Each Short lives in `output/<NN>_<slug>/` with this file set:
```
script.txt          research_notes.json   metadata.txt
youtube_meta.json   narration.mp3         storyboard.md
captions.srt        asset_plan.json       timing.json
headline.txt        thumbnail_notes.txt   edit_plan.json
assets/*.{mp4,jpg}  draft_video.mp4       _bg_gradient.png (fallback)
```

---

## 4. Module-by-module contracts

### llm_client.py — "provider-agnostic LLM transport" (NEW)
- `MODEL_REGISTRY` — flat table, one row per choosable model. Single place to add/edit.
  Keys: `groq-llama33` (default), `groq-deepseek`, `groq-llama4`,
  `nvidia-nemotron-super`, `nvidia-llama33`, `nvidia-gpt-oss-120b`, `nvidia-llama-3-1-70b`.
- `call_llm(messages, model_key, temperature, max_tokens)` — single curl path for
  both Groq and NVIDIA (both speak OpenAI-style `/v1/chat/completions`).
  3 retries + backoff. `--max-time` scales with `max_tokens`.
- `resolve_model(key)` / `list_models()` / `model_keys()` — helpers for CLI.
- `available_models()` — returns model keys whose API keys are present in env.
- Auto-loads `.env` so `GROQ_API_KEY` and `NVIDIA_API_KEY` just work.

### script_generator.py — "the combined call" (the brain)
- **Uses `llm_client.call_llm`** (model key passed from run_pipeline; defaults to
  `groq-llama33` for backward compatibility).
- `_call_llm` is now a thin wrapper; the old curl logic moved to `llm_client`.
- `COMBINED_SYSTEM_PROMPT` Part B expanded: generates `youtube_title` (≤60 chars,
  punchy, YouTube-SEO) and `youtube_description` (≤200 chars + hashtags) alongside
  the 5 headline options and chosen headline.
- `_validate` enforces: banned filler, near-duplicate search_terms, 6-9 sentences,
  `youtube_title` ≤60 chars, `youtube_description` ≤200 chars.
- `generate_combined` returns `{script, headline_options, headline, youtube_title,
  youtube_description, shots, word_count, research}`.
- `process_story(story, content=None)` — public entry; fetches content if needed.

### news_fetcher.py — "discover & rank"
- `RSS_FEEDS` widened: AI/tech feeds + **TLDR AI** + **TLDR Tech/Founders/Crypto** + **CNBC Business**
  + **TechCrunch Startups** + **MarketWatch** + **Reddit RSS** (r/programming, ML, artificial, singularity,
  stocks, investing, wallstreetbets, economics, business via `old.reddit.com/r/{sub}/top/.rss`)
  + **Google News RSS** (3 category queries: AI, Business, Science — redirects resolved for top candidates only).
- `HN_KEYWORDS` regex widened: AI terms + `business|finance|market|stock|earnings|
  startup funding|crypto|bitcoin|economy|inflation|layoff|acquisition|ipo|revenue|fed|interest rate`.
- `NICHE_KEYWORDS` widened additively with the same business/finance terms.
- `SOURCE_CATEGORIES` mapping: every source tagged ai/business/science for picker grouping and logging.
- `ENGAGEMENT_SOURCES` set: sources with native engagement data (HN, Reddit) — others get neutral default.
- `rank_top_stories(top_n=10, candidate_pool=30)`: returns ranked story dicts.
- Story dict keys (contract): `source, title, link, summary, published,
  published_raw, score, hn_points, hn_comments, hn_id, post_id, category`.
- Scoring: recency (exponential decay ~48h, ≤40 pts, soft weight) + niche keyword hits (≤30) +
  normalized engagement per source type (≤30; HN/Reddit native, plain RSS/Google News get 15 pts default).
- Logging: candidate count and average score per category, per-source fetch success counts.

### article_fetcher.py — "fetch real content" (Step 1.5)
- `fetch_article_content(story) -> dict` with `article_text` (≤4000 chars),
  `comments` (≤10, HN via `hn.algolia.com` or Reddit via `.json`), `source_kind`
  (`hn|reddit|rss`), `used_fallback`, `fallback_reason`, `article_chars`,
  `comment_count`, `fetched_at`.
- Extraction: `_extract_article_text(html)` → **trafilatura** (best) →
  **readability-lxml** (fallback) → **stdlib regex HTML-stripper** (always
  available; install both for best quality).
- On ANY fetch failure: falls back to `story['summary']` and **logs** it.

### voice_generator.py — "narration"
- `generate_narration(text, project_dir=...)` → `narration.mp3`.
- Voice `en-US-AndrewNeural`, **+20% rate**, +0Hz pitch (tuned for Shorts).

### storyboard_generator.py — "format the visual plan + timing" (no longer the source)
- `generate_storyboard(script, title, project_dir, sentence_timings=None,
  plan=None, headline=None)`.
- When `plan` + `headline` passed (normal path), uses them directly — **no LLM call**.
  Normalizes to downstream contract: pins each entry to its sentence, ensures
  `search_term` + `media_type` keys, synthesizes `visual` from search_term.
- When `plan` is None (standalone), falls back to `generate_visual_plan()` →
  `llm_client.call_llm` (Groq default).
- Writes: `storyboard.md`, `captions.srt`, `asset_plan.json` (`per_sentence` +
  `headline`), `timing.json`, `headline.txt`, `thumbnail_notes.txt`.

### asset_collector.py — "stock footage + photos" (Pexels, data-conscious)
- Primary entry: `collect_assets_for_plan(plan, project_dir) -> {idx: Path}` —
  downloads ONE asset PER SENTENCE (video OR photo), keyed by sentence index.
- **LLM tag verification:** Before downloading, sends `(search_term, Pexels_tags, alt_text)` to Groq
  for YES/NO relevance check. Only downloads first candidate where LLM returns YES.
- Photos via Pexels *photos* API (`src.portrait` pre-cropped URL). Saved as `.jpg`.
- Local cache at `~/.shorts_clip_cache.json` for both video and photo.
- Data-conservation: `MIN_CLIP_WIDTH=480`, `MAX_DOWNLOAD_BYTES=15MB`, HEAD-check
  `Content-Length` first, streaming download aborts mid-stream over cap.
- Browser `User-Agent` (`PEXELS_USER_AGENT`) required on ALL Pexels calls
  (Cloudflare 403s default Python UA).
- Visual variety enforcement: reorders plan to avoid >2 consecutive clips from same
  visual category (person, screen, abstract, nature, city, object).

### video_assembler.py — "chronological clip-per-sentence assembly" (ffmpeg)
- `assemble_video_simple(project_dir)` → `draft_video.mp4`.
- `_assemble_chronological`: ONE asset PER SENTENCE in order; **HARD MAX durations**:
  video clips 3.0s, photos 1.5s. Longer sentences split into multiple clips.
  Each asset reused ≤2 times per video (tracks usage count).
  **If all assets exhausted (2-use limit), raises RuntimeError** → pipeline re-downloads fresh assets (max 2 retries).
- **Transitions:** `xfade` chain between segments (not concat + fades).
  Transition types vary per cut: `fade`, `wipeleft`, `wiperight`, `slideup`, `slidedown`, `zoomin`.
  Duration: 0.2s (optimized for Shorts pacing).
- Photo segments get Ken-Burns `zoompan` (pre-scaled 1.12×, deterministic pan).
  **~30% zoom-out, ~70% zoom-in** for natural variety.
- **Transition clicks:** Subtle 1000Hz sine clicks (0.02s, 15% volume) at sentence boundaries via `-itsoffset`+`amix`.
- **NO burned-in captions / text** — `captions.srt` is reference only; on-screen
  hook in `headline.txt`; user adds text themselves.
- Output: 1080×1920, H.264 ultrafast CRF 23/28, AAC 128k.

### run_pipeline.py — "orchestrator + I/O"
- `main()`: arg parsing (`--model`, `--auto`, `--no-dedupe`), banner with model.
- `check_prerequisites(model_key)` warns about whichever key the chosen model needs.
- After ranking: daily dedupe (`output/_generated_log.json`) → story picker (interactive unless `--auto`).
- `process_story(story)` called per selected story.
- `save_project` writes `youtube_meta.json` + all other artifacts; logs to daily dedupe log.
- Real narration timing via ffprobe → `_sentence_timings_from_audio` → passed to storyboard.
- **Video assembly retry:** If `_assemble_chronological` raises "assets exhausted" (all clips hit 2-use limit),
  clears `assets/` and re-downloads fresh assets via `collect_assets_for_plan` (max 2 retries).

---

## 5. Known issues & gotchas

Documented so they aren't re-discovered. These are pre-existing, not regressions.

1. **Wrong API key in `check_prerequisites()`** — **FIXED**.
   It used to warn about `NVIDIA_NIM_API_KEY`, but the pipeline uses Groq
   (`GROQ_API_KEY`). NVIDIA key was never used anywhere. Now checks `GROQ_API_KEY`
   and points to console.groq.com in the wording.

2. **Duplicate `-vf` in `_assemble_with_clips()`** — **FIXED (and superseded)**.
   Originally the scale/pad filter and the subtitle filter were passed as two
   separate `-vf` args, so FFmpeg applied only the last one (subtitles),
   dropping the scale/pad. There was ALSO a `NameError` (`output` vs
   `output_path`) on the success print line that crashed assembly entirely
   whenever clips were present. The whole `_assemble_with_clips` path has since
   been REPLACED by `_assemble_chronological` (clip-per-sentence, real timing,
   no burned captions). The gradient fallback is the only remaining simple path.

3. **Thumbnail notes written twice** — **FIXED**.
   `storyboard_generator.generate_storyboard()` now is the single writer of
   `thumbnail_notes.txt`; `run_pipeline.save_project()` only writes it as a
   fallback if the storyboard step itself failed. No more double `✓ thumbnail_notes.txt`.

4. **`save_project()` swallows errors silently per-step.** This is intentional
   for batch resilience (one bad story shouldn't abort the rest), but a step
   that fails leaves the project in a partial state with only a generic
   "✗ FAILED" message and no traceback. When debugging a specific step, add a
   `print(traceback.format_exc())` or run the failing module standalone (each
   module has an `if __name__ == "__main__"` test block).

5. **SRT timing drift** — **FIXED**.
   `captions.srt` timing used to be *estimated* from word counts regardless of
   the actual narration. Now `run_pipeline` probes the real narration duration
   with ffprobe and passes per-sentence timings (`_sentence_timings_from_audio`,
   word-weighted) into `generate_storyboard`, which writes both `timing.json`
   and `captions.srt` from those real times. The assembler reads the SAME
   `timing.json`, so video clips AND the caption file are both aligned to the
   real voiceover. (Word-rate weighting is a heuristic, not true forced
   alignment — accurate enough for clips to line up with sentences; true
   word-level sync would need whisper, out of scope.) Captions are no longer
   burned in either (see the assembler section), so the SRT is a reference only.

6. **Groq/NVIDIA LLM call uses `curl`, not `requests`.** Intentional (one fewer dep at
   the call site) but means error surfaces look like curl/JSON errors, not HTTP
   errors. If script generation fails, check `llm_client.call_llm` retry logs first.

7. **Groq model name is hardcoded in registry** (`llama-3.3-70b-versatile`). If Groq
   deprecates it, every run breaks until `MODEL_REGISTRY` is updated. Consider reading
   from env (`GROQ_MODEL`) with the current value as default. Same for NVIDIA model IDs.

8. **Clip cache is global** (`~/.shorts_clip_cache.json`), shared across runs
   and projects. Reusing clips saves bandwidth but means two unrelated Shorts
   about different topics may share identical background footage. Expected for
   a free-tier tool; note it if reuse matters.

9. **Pexels downloads were silently broken before the data-conservation pass.**
   The original `asset_collector.py` had two latent bugs that meant *no real
   footage was actually being downloaded* — the assembler was quietly falling
   back to the gradient background for every Short:
   - It used `urllib.request.urlretrieve` for downloads, which sends the default
     Python User-Agent. Pexels' Cloudflare front returns **HTTP 403 (error 1010)**
     for that UA, so every download silently returned `None`.
   - The search API call likewise omitted a UA (403) — and the 403 was swallowed
     by a bare `except`, so nothing was logged.
   **Fixed:** both the API search (`_search_pexels`) and the download
   (`_download`, now an explicit streaming `urlopen` with the browser
   `PEXELS_USER_AGENT`) send a browser UA, and download errors are now printed
   instead of swallowed. Verified: 3 real 540×960 clips downloaded & ffprobed
   OK. **Implication:** the pre-existing `draft_video.mp4` files in `output/`
   were assembled on the gradient fallback, NOT real Pexels clips. A fresh
   `--count 1` run is needed to get true video-backed Shorts.

10. **Article/comment fetch falls back silently-ish (Step 1.5).** Introduced by
    `pipeline_upgrade_spec.md`. If `article_fetcher` can't get the article body
    OR the comments (timeout, 403, 404, paywall), it falls back to the RSS
    `summary` and **logs** `✗ content fetch fell back to RSS blurb (…)`. The
    fallback is also persisted into `metadata.txt`'s `fetch` block per project,
    so you can spot a high fallback rate — at which point the script-quality
    problem the upgrade targets will resurface *upstream* of the LLM, and no
    prompt tweak will fix it (you'd be back to title+blurb inputs). Watch this
    metric.

11. **Reddit RSS 429s in this sandbox.** `old.reddit.com/r/{sub}/top/.rss` endpoints
    rate-limit this sandbox's IP. Uses browser UA + 8s delay + 3 retries; works on normal hosts.
    Some subs (r/programming, r/stocks, r/economics, r/singularity) succeed;
    others (r/MachineLearning, r/artificial, r/investing, r/wallstreetbets, r/business)
    may 429. Not a code bug — environmental. Pipeline falls back gracefully to RSS + HN + TLDR.

12. **The combined LLM call needs token + time headroom.** It returns script +
    5 headlines + youtube_title + youtube_description + a 6-9-entry per-sentence
    plan as JSON — bigger than a bare script. `call_llm`'s curl `--max-time` is
    scaled to `max_tokens` (`COMBINED_MAX_TOKENS=2048`); if you shrink either,
    watch for curl exit 28 (timeout mid-JSON) or a truncated response that
    `_parse_combined` rejects.

13. **Script ↔ plan sentence count is coupled by construction.** The script is
    reconstructed by joining the `shots[i].sentence` strings, so sentence count
    and plan length are guaranteed equal. If you ever route the script through a
    *separate* generator (e.g. bring back the two-call split), you MUST re-split
    it identically or the storyboard/assembler will drift (off-by-one segment
    boundaries). The current design intentionally removes that failure mode.

14. **NVIDIA model IDs may change.** The exact strings in the API `model` field
    are publisher/name-version style (e.g. `nvidia/llama-3.1-nemotron-ultra-253b-v1`).
    They can change as NVIDIA updates the catalog — each ID is a one-line edit in
    `llm_client.MODEL_REGISTRY`. Verify at `https://build.nvidia.com/explore/discover`
    if a model errors with "model not found".

15. **Groq daily token limit (100k TPD on free tier).** The combined call uses ~4k tokens.
    After ~20-25 videos you'll hit the limit. Workaround: use NVIDIA models,
    or wait for reset (40 min), or upgrade to Dev Tier at console.groq.com.
    The pipeline shows a clear error with `Retry-After` seconds.

16. **Story picker "list" command removed.** Previously `list` showed all stories
    then re-prompted; now ALL stories are shown by default (grouped by category),
    and `list` is no longer a command. Simpler UX — just pick numbers or `topN`/`all`.

17. **xfade transition chain replaces concat + fades.** The filter graph is now
    a single xfade chain with varied transition types. If ffmpeg version lacks
    `zoomin` transition, it falls back gracefully (but test on target environment).

18. **Asset tag verification adds ~1 LLM call per sentence.** Adds ~50 tokens input
    per sentence (negligible vs combined call). If LLM unavailable (no API key),
    skips verification and accepts all candidates — no hard failure.

19. **Asset fallback retry clears `assets/` directory.** On "assets exhausted" error,
    the pipeline deletes the project's `assets/` folder and re-downloads. This
    means any manually added clips in that folder would be lost — avoid manual
    edits during active generation.

16. **Reddit RSS 429s in this sandbox.** `old.reddit.com` RSS endpoints rate-limit
    this sandbox's IP. Uses browser UA + 8s delay + 3 retries; works on normal hosts.
    Some subs (r/programming, r/stocks, r/economics, r/singularity) succeed;
    others (r/MachineLearning, r/artificial, r/investing, r/wallstreetbets, r/business)
    may 429. Not a code bug — environmental.

---

## 6. How to test / verify changes

Run these in order (fast → slow) to isolate which layer broke:

1. **News layer:** `python news_fetcher.py` → prints top stories with scores.
   (Reddit RSS may `[skip] 429` in this sandbox — see known issue 16.)
2. **Fetch layer:** `python article_fetcher.py [URL]` → fetches a real
   article + comments and prints the extracted text; with no URL, demos on the
   current top HN story. Verify the fallback path by pointing it at a dead URL
   and checking you get `✗ ... fell back to RSS blurb`.
3. **LLM client:** `python llm_client.py --list` — lists all models; `python llm_client.py --model groq-llama33` (needs key) smoke-tests the transport.
4. **Script layer:** `python script_generator.py [URL]` → with a URL, live-fetches
   it and runs the ONE combined LLM call, printing `script`, `headline_options`,
   `chosen_headline`, `youtube_title`, `youtube_description`, and `shots[]`
   (per-sentence `search_term`/`media_type`). With no args, uses an embedded
   test story. Needs `GROQ_API_KEY` (or `NVIDIA_API_KEY` with `--model`).
5. **Storyboard layer:** `python storyboard_generator.py` (reads stdin) →
   writes `test_output/` and prints the shot plan. With no `plan` passed it
   calls LLM for per-sentence visuals (standalone fallback); in the real
   pipeline run_pipeline passes the combined-call plan so no LLM call happens.
6. **Voice layer:** `python voice_generator.py --script "hello" --output /tmp/t.mp3`
7. **Asset layer:** `python asset_collector.py technology /tmp/test_cache` (legacy
   path) OR build a fake `plan` and call `collect_assets_for_plan(plan, dir)`
   (the real per-sentence path — exercises video+photo download + cache). Needs
   `PEXELS_API_KEY`.
8. **Assembler layer:** `python video_assembler.py output/01_test` → needs a
   project with narration + `asset_plan.json` + `timing.json` + `assets/`.
9. **Full quick:** `python run_pipeline.py --quick` (1 story, no video, ~1 min).
10. **Full video:** `python run_pipeline.py --count 1` (real end-to-end, ~5-10 min).
    Verify with ffprobe: `ffprobe output/01_*/draft_video.mp4` — expect 1080x1920,
    duration ≈ narration, video+audio, NO subtitle stream.
11. **Model flag:** `python run_pipeline.py --model groq-deepseek --count 1 --no-video`
    and (if NVIDIA key set) `--model nvidia-nemotron-super --count 1 --no-video`
    or `--model nvidia-gpt-oss-120b --count 1 --no-video`.
12. **Cross-model comparison:** `python run_pipeline.py --compare-models "groq-llama33,nvidia-nemotron-super" --count 1 --no-dedupe`
    runs the SAME story across multiple models (model-specific folders).
13. **Interactive picker:** `python run_pipeline.py --count 5 --no-video` (TTY) → exercises story picker; 
    type `list` to view all without generating; re-run confirms daily dedupe filters them; 
    `--no-dedupe` brings them back; `--auto` skips the prompt.
14. **YouTube meta:** Inspect `youtube_meta.json` in a new project: title (punchy, ≤60), 
    description (≤200, +hashtags), on_screen_hook present.

Each module's `__main__` block is a self-contained smoke test — prefer running
those over hacking a full pipeline run when debugging one stage.

---

## 7. Reasoning hooks for adding features

Keep these patterns in mind so new code fits the project's conventions:

- **Add a news source → edit `RSS_FEEDS` (RSS) or `REDDIT_SUBS` (Reddit), or add
  a new `fetch_*` function** in `news_fetcher.py`. Preserve the story-dict keys
  the contract depends on (`hn_points`/`hn_comments` for engagement, plus `hn_id`
  for HN or `post_id` for Reddit so `article_fetcher` can pull comments).
- **Improve article extraction → swap the backend in
  `article_fetcher._extract_article_text`**. Priority chain is trafilatura →
  readability-lxml → stdlib regex; each is optional and degrades to the next.
  Both are in `requirements.txt` now. Update the `trafilatura.extract` flags or
  add a new backend (e.g. `goose3`) ahead of the stdlib fallback.
- **Change the niche → edit `NICHE_KEYWORDS`** (relevance scoring) and the
  fallback `RELATABLE_TERMS`/`_term_from_sentence` in `storyboard_generator`
  (so fallback asset searches stay on-niche). Tweak `COMBINED_SYSTEM_PROMPT`
  Part C so Groq's per-sentence search terms stay on-niche. The Part A jargon
  banlist may also need updating.
- **Swap LLM provider → replace `_call_llm`** in `script_generator.py`. The
  storyboard's standalone fallback (`generate_visual_plan`) imports it lazily
  (`from script_generator import _call_llm as _call_groq`). Keep the retry +
  JSON-parsing-with-fence-stripping behavior; bump `max_tokens`/`max-time` for
  the combined call's bigger response (see known issue 12).
- **Split the combined call back out (or merge more in) →** the combined
  `generate_combined` is the single source of truth; the storyboard consumes its
  `shots`/`headline` and makes no Groq call. If you split script vs. keywords
  again, you must re-split the script identically or the storyboard/assembler
  drift (the joined-sentences trick in `process_story` removes that today — see
  known issue 13). Banned-filler + near-duplicate validators live in
  `_validate`; widen `BANNED_FILLER` to catch more lazy openers/closers.
- **Better clip-word alignment →** current timing is word-rate weighted
  (robust heuristic, sentence-level). For true word-level sync, replace
  `_sentence_timings_from_audio` with forced alignment via `whisper`/`stable-ts`
  against `narration.mp3`. The per-sentence asset plan already lets you cut one
  clip per sentence; this just sharpens the boundaries.
- **Real crossfades between sentences →** the assembler currently concat-enates
  per-segment clips with short fade in/out (visually seamless). For genuine
  cross-dissolves, swap the `concat` for an `xfade` filtergraph across segment
  pairs. `_assemble_chronological` already builds per-segment labels, so an xfade
  chain drops in here.
- **A new per-project artifact → add it in `save_project()`** following the
  existing try/except-print-FAILED pattern, so a failure can't abort the batch.
- **Backpressure / rate limits →** Groq is 30 req/min. After the upgrade a story
  is ONE combined call (+ maybe 1 validator retry), so ~1-2 calls/story — half
  the old ~4 (research + script + rewrite + per-sentence plan). Article/comment
  fetching is free public JSON; Reddit throttles (hence `REDDIT_REQUEST_DELAY`).
  If you ever hit Groq 429s, add a small `time.sleep` between stories in
  `main()`'s loop, not inside `_call_llm` (which already retries).
- **Thumbnails / actual rendering →** this project only *describes* thumbnails
  in text notes. A natural Phase 2 is generating the thumbnail image (PIL) from
  the notes + the per-sentence `key_numbers`. Put it in a new
  `thumbnail_generator.py` and call from `save_project()`.
- **Captions →** the user adds captions themselves; the pipeline deliberately
  does NOT burn them in. `captions.srt` is generated as a reference side file,
  timed from the real narration. If you ever want to bring burned captions back,
  re-add a `subtitles=` filter to the assembler's `-vf` — but it was removed on
  request.

---

## 8. File map

```
run_pipeline.py          Orchestrator + per-project I/O + real-timing + edit/BGM helpers + story picker + daily dedupe + youtube_meta.json
llm_client.py            Provider-agnostic LLM transport (Groq + NVIDIA NIM), MODEL_REGISTRY, call_llm, available_models()
news_fetcher.py          RSS (incl. TLDR AI/Tech/Founders/Crypto, CNBC, TechCrunch Startups, MarketWatch, Reddit RSS) + HN discovery + ranking (Step 1)
article_fetcher.py       Fetch real article text + HN/Reddit comments (Step 1.5);
                         trafilatura→readability→stdlib with RSS-summary fallback
script_generator.py      ONE combined LLM call: script + 5 headlines + youtube_title + youtube_description + per-sentence search terms (Steps 2-3); validator with 1 retry; uses llm_client
voice_generator.py       Edge-TTS narration (Step 4)
storyboard_generator.py   Formats the combined call's plan → shot list + SRT + timing.json
                         (Steps 5-6; NO LLM call when plan passed; reuses llm_client only as standalone fallback)
asset_collector.py       Pexels video+photo download, one PER SENTENCE + cache (Step 6)
video_assembler.py       FFmpeg chronological clip-per-sentence MP4 + zoompan photos,
                         HARD MAX: video 3.0s, photo 1.5s; asset ≤2× reuse; click transitions
                         NO burned captions (Step 7)
pipeline_upgrade_spec.md The upgrade spec this phase implemented (§1 fetch, §2 sources, §3 combined call + validator) — reference, not loaded at runtime
requirements.txt         Python deps (requests, edge-tts, Pillow, trafilatura, readability-lxml)
.env                     Secrets (GROQ_API_KEY, PEXELS_API_KEY, NVIDIA_API_KEY) — not committed
output/                  Generated Shorts, one folder each — not committed
```

---

## 9. Conventions

- Each module is independently runnable (`python <module>.py` smoke tests).
- Secrets stay in `.env`; never hardcode, never echo values, never commit.
- Per-step failures in the batch are logged and skipped, not raised — keeps a
  bad story from wrecking the whole run.
- Prefer stdlib + `subprocess` over new dependencies; pip deps are `requests`,
  `edge-tts`, `Pillow`, plus `trafilatura`/`readability-lxml` for article
  extraction (optional in spirit — both have a stdlib fallback in
  `article_fetcher` — but listed in requirements.txt since they materially
  improve script quality).
- Story/research/script/json dicts are the public contract between modules —
  don't rename their keys casually; downstream code reads them by name.
