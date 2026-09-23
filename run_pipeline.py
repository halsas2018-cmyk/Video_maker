"""
run_pipeline.py
PHASE 1 — Automated pipeline: discover -> research -> script -> voice ->
storyboard -> captions -> assets -> stage for Remotion.

The pipeline prepares all assets and props in the public/ directory.
Use --render flag to automatically render with Remotion after staging.
Without --render, use: npx remotion render ShortsComposition [output.mp4] --props=remotion_props.json

Requirements (set in .env file or export):
    GROQ_API_KEY="gsk-..."            # required — get at console.groq.com
    PEXELS_API_KEY="your-key"          # optional — get at pexels.com/api
    NVIDIA_API_KEY="your-key"          # optional — get at build.nvidia.com

Usage:
    python run_pipeline.py --count 3 --outdir output
    python run_pipeline.py --model groq-gpt-oss-20b --count 1
    python run_pipeline.py --auto --count 5
    python run_pipeline.py --auto --render --count 3  # Stage + render

Flags:
    --count       Number of videos to produce (default: 3, max: 10)
    --outdir      Output directory (default: output)
    --no-video    Skip asset download & staging (scripts + voice only)
    --quick       Minimal output — 1 script for quick review
    --model       LLM model key (default: gemini-31-flash-lite). See `python llm_client.py --list`
    --rank-model  LLM key for the editorial rerank ONLY (default: same as --model).
                  e.g. --rank-model nvidia-nemotron-ultra keeps scripts on Groq
                  while ranking runs on NVIDIA (separate rate limits)
    --auto        Don't prompt for story selection; generate top-N automatically
    --no-dedupe   Don't filter out stories already generated today
    --no-llm-rank Skip the LLM editorial rerank; rank by heuristic score only
    --render      Render video with Remotion after staging assets (default: stage only)
"""

import argparse
import json
import os
import re
import subprocess
import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Auto-load .env file (so you don't need to 'export' every time)
# ---------------------------------------------------------------------------
_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    for _line in _env_path.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _key, _val = _line.split("=", 1)
            _key, _val = _key.strip(), _val.strip().strip('"').strip("'")
            if _key and not os.environ.get(_key):
                os.environ[_key] = _val

# Import llm_client early so model keys can be validated in CLI
import llm_client

import llm_ranker
from news_fetcher import rank_top_stories
from script_generator import process_story
from voice_generator import generate_narration
from storyboard_generator import generate_storyboard
from asset_collector import collect_assets, collect_assets_for_plan, collect_assets_for_plan_with_fallback
from remotion_assembler import assemble_video_remotion


def slugify(text: str, max_len: int = 50) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", text.strip().lower()).strip("_")
    return slug[:max_len] or "untitle"


def _get_daily_outdir(base_outdir: Path) -> Path:
    """Get the daily output directory (e.g., output/09_08_short_vids)."""
    today = date.today()
    # Format: DD_MM_short_vids (e.g., 09_08_short_vids for August 9th)
    daily_dir_name = f"{today.day:02d}_{today.month:02d}_short_vids"
    daily_dir = base_outdir / daily_dir_name
    daily_dir.mkdir(parents=True, exist_ok=True)
    return daily_dir


# ---------------------------------------------------------------------------
# Daily dedupe log (output/09_08_short_vids/_generated_log.json)
# ---------------------------------------------------------------------------

def _get_dedupe_log_path(base_outdir: Path) -> Path:
    """Get the daily dedupe log path inside the daily output directory."""
    daily_dir = _get_daily_outdir(base_outdir)
    return daily_dir / "_generated_log.json"


def _load_dedupe_log(base_outdir: Path) -> list[dict]:
    """Load the daily dedupe log, return empty list on any error."""
    log_path = _get_dedupe_log_path(base_outdir)
    try:
        if log_path.exists():
            content = log_path.read_text(encoding="utf-8")
            if content.strip():
                return json.loads(content)
    except Exception:
        pass
    return []


def _save_dedupe_log(base_outdir: Path, entries: list[dict]):
    """Save the dedupe log, trimming to last ~30 days."""
    log_path = _get_dedupe_log_path(base_outdir)
    try:
        today = date.today().isoformat()
        # Keep only last 30 days
        cutoff = date.fromisoformat(today)
        from datetime import timedelta
        cutoff = cutoff - timedelta(days=30)
        filtered = [
            e for e in entries
            if e.get("date") and date.fromisoformat(e["date"]) >= cutoff
        ]
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(json.dumps(filtered, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"  [warn] Could not save dedupe log: {e}")


def _fingerprint(story: dict) -> str:
    """Generate a fingerprint for a story (same normalization as dedupe)."""
    title = story.get("title", "")
    return re.sub(r"\W+", "", title.lower())[:60]


def _filter_deduped_today(base_outdir: Path, stories: list[dict]) -> tuple[list[dict], int]:
    """Filter out stories already generated today. Returns (filtered, count_removed)."""
    log = _load_dedupe_log(base_outdir)
    today = date.today().isoformat()
    today_keys = {e["key"] for e in log if e.get("date") == today}
    filtered = []
    removed = 0
    for s in stories:
        fp = _fingerprint(s)
        if fp in today_keys:
            removed += 1
        else:
            filtered.append(s)
    return filtered, removed


def _log_generated_story(base_outdir: Path, story: dict, model_key: str, project_slug: str):
    """Append a successful generation to the daily dedupe log."""
    log = _load_dedupe_log(base_outdir)
    today = date.today().isoformat()
    log.append({
        "date": today,
        "key": _fingerprint(story),
        "title": story.get("title", "")[:120],
        "link": story.get("link", ""),
        "model": model_key,
        "project": project_slug,
    })
    _save_dedupe_log(base_outdir, log)


def _audio_duration(path: Path) -> float:
    """Measure real narration duration via ffprobe (~robust forced-align
    proxy: we distribute this real duration across sentences by word weight)."""
    try:
        out = __import__("subprocess").run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=10,
        )
        return float(out.stdout.strip())
    except Exception:
        return 0.0


def _sentence_timings_from_audio(script: str, total_duration: float) -> list[tuple]:
    """Distribute the REAL narration duration across sentences by word count.

    Returns a list of (start, end) floats, one per sentence. Word-rate weighting
    is a robust proxy for forced alignment — accurate enough that clips line up
    with the spoken words. (True forced alignment would need whisper; out of scope.)
    """
    sentences = re.split(r'(?<=[.!?])\s+', script.strip())
    sentences = [s.strip() for s in sentences if s.strip()]
    if not sentences or total_duration <= 0:
        return []
    word_counts = [len(s.split()) for s in sentences]
    total_words = sum(word_counts)
    if total_words <= 0:
        return [(0.0, total_duration)]
    timings, cur = [], 0.0
    for wc in word_counts:
        dur = total_duration * (wc / total_words)
        timings.append((cur, cur + dur))
        cur += dur
    # clamp last end to the real audio length (no rounding overshoot)
    if timings:
        s, _ = timings[-1]
        timings[-1] = (s, total_duration)
    return timings


def _sentence_timings_from_word_timestamps(words: list[dict],
                                            sentences: list[str],
                                            fallback_total_dur: float = None) -> list[tuple]:
    """Derive per-sentence (start, end) timings from WhisperX word-level timestamps.

    Walks the word list and matches each word to the corresponding word in the
    sentence list (case-insensitive, punctuation stripped). A sentence boundary
    is reached when the next sentence's first word is encountered.

    Falls back to the word-count proxy (`_sentence_timings_from_audio`) if word
    matching fails for any sentence — e.g. WhisperX dropped a word, TTS pronounced
    a word differently, or the script/sentences list is empty.

    Args:
        words: WhisperX output — list of {"word": str, "start": float, "end": float}.
        sentences: The script's sentences (already split, in order).
        fallback_total_dur: If provided, used to seed the proxy fallback's total
            duration (typically the real narration duration from ffprobe).

    Returns:
        list of (start, end) floats, one per sentence.
    """
    if not sentences or not words:
        if fallback_total_dur:
            return _sentence_timings_from_audio(__sentences_joined(sentences), fallback_total_dur)
        return []

    # Normalize: strip punctuation/case for matching.
    def _clean(w: str) -> str:
        return re.sub(r"[^\w']", "", w.lower()).strip("'")

    cleaned_words = [_clean(w.get("word", "")) for w in words]
    # Split sentences into words first, then clean each word — avoids collapsing
    # spaces when the regex strips punctuation from the whole sentence string.
    cleaned_sents = [[_clean(w) for w in s.split()] for s in sentences]

    timings = []
    word_idx = 0
    success = True

    for sent_words in cleaned_sents:
        sent_words = [w for w in sent_words if w]
        if not sent_words:
            timings.append((0.0, 0.0))
            success = False
            break

        # Find the start of this sentence in the word stream.
        start_time = None
        end_time = None
        matched = 0

        while word_idx < len(cleaned_words) and matched < len(sent_words):
            if cleaned_words[word_idx] == sent_words[matched]:
                if start_time is None:
                    start_time = words[word_idx].get("start", 0.0)
                end_time = words[word_idx].get("end", start_time)
                matched += 1
            word_idx += 1

        if matched < len(sent_words):
            # Didn't match all expected words for this sentence.
            success = False
            break

        # If the next word in the stream is punctuation (e.g. "."), grab its
        # end time so sentence boundaries align precisely with the spoken pause.
        while word_idx < len(cleaned_words) and cleaned_words[word_idx] == "":
            end_time = words[word_idx].get("end", end_time)
            word_idx += 1

        if start_time is not None and end_time is not None:
            timings.append((float(start_time), float(end_time)))
        else:
            success = False
            break

    if success and len(timings) == len(sentences):
        # Clamp last end to the real last word's end (no overshoot).
        timings[-1] = (timings[-1][0], words[-1].get("end", timings[-1][1]))
        return timings

    # Fallback: use the word-count proxy with the real total duration.
    if fallback_total_dur:
        return _sentence_timings_from_audio(__sentences_joined(sentences), fallback_total_dur)
    # Last resort: derive total from last word.
    total = words[-1].get("end", 0.0) if words else 0.0
    if total > 0:
        return _sentence_timings_from_audio(__sentences_joined(sentences), total)
    return []


def __sentences_joined(sentences: list[str]) -> str:
    """Join sentences with spaces (mirrors script_generator's concatenation)."""
    return " ".join(sentences).strip()


def _split_script_into_sentences(script: str) -> list[str]:
    """Split a script into sentences using the same logic as storyboard_generator."""
    import re as _re
    parts = _re.split(r'(?<=[.!?])\s+', script.strip())
    return [p.strip() for p in parts if p.strip()]


def check_prerequisites(model_key: str = llm_client.DEFAULT_MODEL_KEY):
    """Check API key and critical dependencies before starting.

    Script generation uses the key for the chosen model.
    """
    try:
        row = llm_client.resolve_model(model_key)
    except ValueError as e:
        print(f"ERROR: {e}")
        return False

    key_env = row.get("key_env", "")
    if not os.environ.get(key_env):
        print("=" * 60)
        provider = row.get("provider", "").upper()
        signup = {"groq": "https://console.groq.com", "nvidia": "https://build.nvidia.com"}.get(
            row.get("provider", ""), ""
        )
        print(f"WARNING: {key_env} is not set.")
        print(f"Script + visual-plan generation require this {provider} key.")
        print()
        if signup:
            print(f"Get a free key at: {signup}")
        print(f"Put it in .env:  {key_env}=\"<your-key>\"")
        print("=" * 60)
        print()
        return False
    return True


def _get_daily_outdir(base_outdir: Path) -> Path:
    """Get the daily output directory (e.g., output/09_08_short_vids)."""
    today = date.today()
    # Format: DD_MM_short_vids (e.g., 09_08_short_vids for August 9th)
    daily_dir_name = f"{today.day:02d}_{today.month:02d}_short_vids"
    daily_dir = base_outdir / daily_dir_name
    daily_dir.mkdir(parents=True, exist_ok=True)
    return daily_dir


def save_project(result: dict, outdir: Path, index: int, no_video: bool = False, model_key: str = llm_client.DEFAULT_MODEL_KEY, render_hook_text: bool = False):
    """
    Save all project files for one Short.

    Creates:
        daily_dir/
        ├── project/
        │   ├── script.txt          — Narration script
        │   ├── research_notes.json — Fact-checked research
        │   ├── metadata.txt        — Title, source, score, link
        │   ├── narration.mp3       — Edge TTS audio (if not no_video)
        │   ├── storyboard.md       — Per-shot visual plan
        │   ├── captions.srt        — Timed subtitles
        │   ├── asset_plan.json     — Keywords + stock search terms
        │   ├── thumbnail_notes.txt — Thumbnail suggestions
        │   ├── edit_plan.json      — Full editing instructions
        │   └── draft_video.mp4     — Assembled video (if not no_video)
    """
    story = result["story"]
    # Get daily directory (e.g., output/09_08_short_vids/)
    daily_dir = _get_daily_outdir(outdir)
    # Format: MMDD_XX_model_slug (e.g., 08_09_01_groq-gpt-oss-120b_oracle_bans_ai_code)
    today = date.today()
    date_str = f"{today.month:02d}_{today.day:02d}"
    model_slug = slugify(model_key.replace(".", "-"))
    project_name = f"{date_str}_{index:02d}_{model_slug}_{slugify(story['title'])}"
    project_dir = daily_dir / project_name
    project_dir.mkdir(parents=True, exist_ok=True)
    assets_dir = project_dir / "assets"
    assets_dir.mkdir(exist_ok=True)

    print(f"\n  ┌─ {'='*50}")
    print(f"  │  Project: {project_name}")
    print(f"  └─ {'='*50}")

    # --- Script ---
    script_path = project_dir / "script.txt"
    script_path.write_text(result["script"], encoding="utf-8")
    print(f"  ✓ script.txt ({result['word_count']} words)")

    # --- Research notes ---
    (project_dir / "research_notes.json").write_text(
        json.dumps(result["research"], indent=2), encoding="utf-8"
    )
    print(f"  ✓ research_notes.json")

    # --- Metadata ---
    # The combined script call also reports fetch quality (article/comment
    # fetch fallback rate — see pipeline_upgrade_spec.md). Surface it here so
    # you can spot when the script quality problem resurfaces upstream.
    fetch = result.get("research", {}) if isinstance(result.get("research"), dict) else {}
    metadata = {
        "title": story["title"],
        "source": story["source"],
        "link": story["link"],
        "score": story.get("score"),
        "score_details": {k: v for k, v in story.items()
                          if k in ("recency_score", "relevance_score",
                                   "engagement_score")},
        "word_count": result["word_count"],
        "headline": result.get("headline", ""),
        "fetch": {
            "source_kind": fetch.get("source_kind", "rss"),
            "used_fallback": fetch.get("used_fallback", False),
            "fallback_reason": fetch.get("fallback_reason", ""),
            "article_chars": fetch.get("article_chars", 0),
            "comment_count": fetch.get("comment_count", 0),
            "fetched_at": fetch.get("fetched_at", ""),
        },
        "generated_at": __import__("datetime").datetime.now().isoformat(),
    }
    (project_dir / "metadata.txt").write_text(
        json.dumps(metadata, indent=2), encoding="utf-8"
    )
    print(f"  ✓ metadata.txt")

    # --- YouTube metadata (new unified file) ---
    youtube_meta = {
        "youtube_title": result.get("youtube_title", ""),
        "youtube_description": result.get("youtube_description", ""),
        "on_screen_hook": result.get("headline", ""),
        "headline_options": result.get("headline_options", []),
        "video_title_source": story["title"],
        "source_link": story["link"],
    }
    (project_dir / "youtube_meta.json").write_text(
        json.dumps(youtube_meta, indent=2), encoding="utf-8"
    )
    print(f"  ✓ youtube_meta.json")

    if no_video:
        return project_dir, 0

    # --- Voice generation ---
    narration_path = project_dir / "narration.mp3"
    print(f"  ─ Voice generation...")
    try:
        generate_narration(result["script"], project_dir=project_dir, format=result.get("format"))
        print(f"  ✓ narration.mp3")
    except Exception as e:
        print(f"  ✗ narration.mp3 FAILED: {e}")

    # --- WhisperX Timestamp Extraction (before timing.json) ---
    timestamps = []
    if narration_path.exists():
        print(f"  ─ Extracting word timestamps with WhisperX...")
        try:
            whisper_py = "/root/kinetic_typo_vid/venv/bin/python3"
            extractor = project_dir / "extract_word_timestamps.py"
            if not extractor.exists():
                import shutil
                shutil.copy("extract_word_timestamps.py", extractor)

            subprocess.run(
                [whisper_py, str(extractor), str(narration_path), "--output", str(project_dir / "timestamps.json")],
                check=True,
                capture_output=True,
                text=True
            )
            print(f"  ✓ timestamps.json (WhisperX)")
            # Load the timestamps for sentence-level timing derivation.
            timestamps_path = project_dir / "timestamps.json"
            if timestamps_path.exists():
                timestamps = json.loads(timestamps_path.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"  ⚠ WhisperX timestamp extraction failed: {e}")
            (project_dir / "timestamps.json").write_text("[]", encoding="utf-8")
            timestamps = []

    # --- Measure REAL narration timing (drives storyboard + assembly sync) ---
    # Prefer accurate per-sentence timings derived from WhisperX word timestamps.
    # Fall back to the word-count proxy if timestamps.json is missing/empty.
    sentence_timings = []
    if timestamps:
        sentences = _split_script_into_sentences(result["script"])
        sentence_timings = _sentence_timings_from_word_timestamps(
            timestamps, sentences, fallback_total_dur=_audio_duration(narration_path)
        )
        print(f"  · timing {len(sentence_timings)} sentence(s) from WhisperX word timestamps")
    elif narration_path.exists():
        real_dur = _audio_duration(narration_path)
        if real_dur > 0:
            sentence_timings = _sentence_timings_from_audio(
                result["script"], real_dur
            )
            print(f"  · narration is {real_dur:.1f}s; timing {len(sentence_timings)} "
                  f"sentence(s) from real audio (word-count proxy)")
        else:
            print(f"  · couldn't probe narration duration; timing will be estimated")

    # --- Storyboard + Captions + per-sentence Asset plan ---
    # The combined script_generator call already produced the per-sentence plan
    # (result["shots"], each with search_term/media_type) AND the chosen
    # headline (result["headline"]). Pass them straight into the storyboard so
    # it does NOT make a second Groq visual-plan call (the spec merged the two).
    print(f"  ─ Generating storyboard & captions...")
    sb_result = None
    try:
        sb_result = generate_storyboard(
            result["script"], story["title"], project_dir,
            sentence_timings=sentence_timings,
            plan=result.get("shots"),
            headline=result.get("headline"),
        )
        for f in sb_result["files_written"]:
            print(f"  ✓ {Path(f).name}")
    except Exception as e:
        print(f"  ✗ Storyboard FAILED: {e}")
        # thumbnail_notes.txt is written by the storyboard generator (part of
        # files_written). If the storyboard step itself failed, fall back to the
        # run-pipeline version so the project still has thumbnail guidance.
        try:
            thumbnail_notes = _generate_thumbnail_notes(story, result)
            (project_dir / "thumbnail_notes.txt").write_text(
                thumbnail_notes, encoding="utf-8"
            )
            print(f"  ✓ thumbnail_notes.txt (fallback)")
        except Exception as te:
            print(f"  ✗ thumbnail_notes.txt FAILED: {te}")

    # --- Edit plan ---
    try:
        edit_plan = _generate_edit_plan(result, sb_result)
        (project_dir / "edit_plan.json").write_text(
            json.dumps(edit_plan, indent=2), encoding="utf-8"
        )
        print(f"  ✓ edit_plan.json")
    except Exception as e:
        print(f"  ✗ edit_plan.json FAILED: {e}")

    # --- Asset collection (Pexels free stock — one asset PER SENTENCE) ---
    asset_plan = sb_result.get("asset_plan", []) if sb_result else []
    assets_downloaded = 0
    if asset_plan:
        print(f"  ─ Downloading {len(asset_plan)} per-sentence asset(s) from Pexels...")
        try:
            asset_result = collect_assets_for_plan_with_fallback(asset_plan, project_dir)
            assets_downloaded = len(asset_result)
        except Exception as e:
            print(f"  ✗ Asset download FAILED: {e}")
    else:
        # Legacy flat-keyword path if no per-sentence plan was produced.
        keywords = sb_result.get("keywords", []) if sb_result else []
        if keywords:
            print(f"  ─ Downloading stock footage from Pexels (legacy)...")
            try:
                collect_assets(keywords, project_dir)
            except Exception as e:
                print(f"  ✗ Asset download FAILED: {e}")
        else:
            print(f"  ─ No keywords for asset search")

    # --- Stage assets to public directory for Remotion (skip actual rendering) ---
    if narration_path.exists():
        print(f"  ─ Staging assets to public directory...")
        try:
            import shutil

            # Stage assets into Remotion public/ project_assets/ folder
            public_assets_dir = Path(__file__).parent / "public" / "project_assets"
            if public_assets_dir.exists():
                shutil.rmtree(public_assets_dir)
            public_assets_dir.mkdir(parents=True, exist_ok=True)

            # Copy narration to public/
            narration_src = narration_path
            narration_dst = public_assets_dir / "narration.mp3"
            shutil.copy(narration_src, narration_dst)
            print(f"  ✓ staged narration.mp3")

            # Copy timestamps to public/
            timestamps_path = project_dir / "timestamps.json"
            if timestamps_path.exists():
                shutil.copy(timestamps_path, public_assets_dir / "timestamps.json")
                print(f"  ✓ staged timestamps.json")

            # Load asset plan
            asset_plan_path = project_dir / "asset_plan.json"
            shots = []
            if asset_plan_path.exists():
                try:
                    data = json.loads(asset_plan_path.read_text(encoding="utf-8"))
                    if isinstance(data, dict):
                        shots = (
                            data.get("per_sentence")
                            or data.get("asset_plan")
                            or data.get("head", [])
                        )
                    elif isinstance(data, list):
                        shots = data
                except Exception:
                    pass

            if not shots:
                print(f"  ⚠ No shots loaded from asset_plan.json")

            # Copy downloaded assets into public/project_assets/ and prepare props
            assets_dir = project_dir / "assets"
            manifest_path = project_dir / "assets_manifest.json"
            manifest = {}
            if manifest_path.exists():
                try:
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                except Exception:
                    pass

            enriched_shots = []
            for idx, s in enumerate(shots):
                match_path = None

                # 1) Prefer the manifest (index -> filename) when available.
                staged_name = manifest.get(str(idx))
                if staged_name:
                    cand = assets_dir / staged_name
                    if cand.exists():
                        match_path = cand

                # 2) Fallback: sequential shot_{idx+1}.{ext} naming.
                if not match_path:
                    for ext in ["mp4", "jpg", "png", "webp"]:
                        p = assets_dir / f"shot_{idx+1}.{ext}"
                        if p.exists():
                            match_path = p
                            break

                # 3) Last resort: whatever sits at position idx (best-effort, unordered).
                if not match_path and assets_dir.exists():
                    files = sorted(
                        [f for f in assets_dir.iterdir()
                         if f.suffix.lower() in {".mp4", ".jpg", ".png", ".webp"}],
                        key=lambda f: f.name,
                    )
                    if idx < len(files):
                        match_path = files[idx]

                staged_rel_path = ""
                if match_path and match_path.exists():
                    dest_name = f"shot_{idx+1}{match_path.suffix}"
                    shutil.copy(match_path, public_assets_dir / dest_name)
                    staged_rel_path = f"project_assets/{dest_name}"
                    print(f"  ✓ staged {dest_name}")

                enriched_shots.append({
                    "sentence": s.get("sentence", ""),
                    "search_term": s.get("search_term", ""),
                    "media_type": s.get("media_type", "video"),
                    "visual": s.get("visual", ""),
                    "asset_path": staged_rel_path,
                    "duration_seconds": s.get("duration_seconds", 3.0),
                })

            # Write remotion props for later rendering
            words = []
            if timestamps_path.exists():
                try:
                    words = json.loads(timestamps_path.read_text(encoding="utf-8"))
                except Exception:
                    pass

            props = {
                "shots": enriched_shots,
                "words": words,
                "narrationSrc": "project_assets/narration.mp3",
            }

            # Write to project directory
            props_json_path = project_dir / "remotion_props.json"
            props_json_path.write_text(json.dumps(props, indent=2), encoding="utf-8")
            print(f"  ✓ generated remotion_props.json")

            # Also publish to public/ for Studio mode
            try:
                public_props_path = Path(__file__).parent / "public" / "remotion_props.json"
                public_props_path.write_text(json.dumps(props, indent=2), encoding="utf-8")
                print(f"  ✓ published remotion_props.json to public/")
            except Exception as e:
                print(f"  ⚠ Could not publish to public/: {e}")

            print(f"  ✓ Assets staged to public directory (skipping Remotion render)")
        except Exception as e:
            print(f"  ⚠ Asset staging FAILED: {e}")
    else:
        print(f"  ─ Skipping asset staging (no narration available)")

    return project_dir, assets_downloaded


def _generate_thumbnail_notes(story: dict, result: dict) -> str:
    """Generate thumbnail design notes from the story."""
    title = story.get("title", "")
    research = result.get("research", {})

    lines = [
        f"# Thumbnail Notes: {title}",
        "",
        "## Key Visual Elements",
        f"- Main subject: {research.get('who_announced', title.split()[0] if title else 'Tech')}",
        f"- Eye-catching number: {research.get('key_numbers', '')}",
        "",
        "## Text Overlay Suggestions",
        "- Bold, 3-5 words max",
        "- High contrast (white text with black stroke)",
        f'- Example: "{_generate_thumbnail_text(title, research)}"',
        "",
        "## Color Palette",
        "- Background: Dark (attracts more attention in Shorts feed)",
        "- Accent: Bright orange or cyan for highlights",
        "- Text: White + yellow for emphasis",
        "",
        "## Composition",
        "- Subject on right or center",
        "- Text on left (read naturally left-to-right)",
        "- High contrast, one focal point",
        "- No clutter — thumbnails are viewed at very small size",
        "",
        "## DO NOT",
        "- Use misleading imagery",
        "- Use copyrighted characters or logos as focal point",
        "- Crowd with too much text",
    ]
    return "\n".join(lines)


def _generate_thumbnail_text(title: str, research: dict) -> str:
    """Generate a punchy 3-5 word thumbnail overlay text."""
    numbers = research.get("key_numbers", "")
    if numbers:
        # Extract first number
        import re
        nums = re.findall(r'\d+[%kKxX]?', numbers)
        if nums:
            return f"{nums[0]}x Faster?"
    return "Game Changer?"


def _generate_edit_plan(result: dict, sb_result: dict) -> dict:
    """Generate editing instructions JSON."""
    story = result.get("story", {})
    script = result.get("script", "")
    shots = sb_result.get("shots", []) if sb_result else []

    word_count = len(script.split())
    estimated_duration = max(20, word_count / 2.8)

    return {
        "project_title": story.get("title", ""),
        "format": "YouTube Shorts (9:16, 1080x1920)",
        "estimated_duration_seconds": round(estimated_duration, 1),
        "narration_file": "narration.mp3",
        "voice": "en-US-AndrewNeural (+10% rate)",
        "editing_software_recommendations": [
            "DaVinci Resolve (free)",
            "CapCut Desktop (free)",
            "Shotcut (free, open-source)",
            "Kdenlive (free, open-source)",
        ],
        "scenes": [
            {
                "shot": s["shot"],
                "duration": s["duration_seconds"],
                "narration": s["text"],
                "visual": s["visual_suggestion"],
                "transition": s["transition"],
                "zoom": s["zoom_effect"],
                "caption_style": {
                    "font": "Arial Bold",
                    "size": 48,
                    "color": "#FFFFFF",
                    "background": "#80000000",
                    "position": "center bottom",
                }
            }
            for s in shots
        ],
        "bgm_recommendation": _suggest_bgm(script, story.get("title", "")),
        "export_settings": {
            "resolution": "1080x1920",
            "fps": 30,
            "codec": "H.264",
            "bitrate": "8 Mbps",
            "audio_bitrate": "128 kbps AAC",
            "format": "MP4",
        },
    }


def _suggest_bgm(script: str, title: str) -> dict:
    """Suggest background music based on script content."""
    combined = (title + " " + script).lower()

    upbeat_indicators = ["launch", "breakthrough", "new", "release", "fast",
                          "record", "growth", "announced"]
    serious_indicators = ["warning", "risk", "danger", "concern", "problem",
                          "crash", "failure", "threat"]

    upbeat_count = sum(1 for w in upbeat_indicators if w in combined)
    serious_count = sum(1 for w in serious_indicators if w in combined)

    if serious_count > upbeat_count:
        return {
            "mood": "documentary / informative",
            "genre": "ambient electronic, low-key",
            "volume": "-25dB relative to narration",
            "free_sources": [
                "YouTube Audio Library",
                "Pixabay Music (pixabay.com/music)",
                "Uppbeat (free tier)",
            ],
        }
    elif upbeat_count > 2:
        return {
            "mood": "upbeat / energetic",
            "genre": "electronic, lo-fi upbeat",
            "volume": "-20dB relative to narration",
            "free_sources": [
                "YouTube Audio Library",
                "Pixabay Music (pixabay.com/music)",
                "Uppbeat (free tier)",
            ],
        }
    else:
        return {
            "mood": "neutral educational",
            "genre": "calm lo-fi, ambient tech",
            "volume": "-22dB relative to narration",
            "free_sources": [
                "YouTube Audio Library",
                "Pixabay Music (pixabay.com/music)",
            ],
        }


def main():
    parser = argparse.ArgumentParser(
        description="YouTube Shorts AI Agent — Phase 1 Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
Examples:
  python run_pipeline.py --count 3                    # 3 full videos
  python run_pipeline.py --count 1 --no-video         # 1 script only, no video
  python run_pipeline.py --count 5 --outdir my_videos # custom output dir
  python run_pipeline.py --model groq-gpt-oss-20b --count 1  # fast/cheap Groq sibling
  python run_pipeline.py --auto --count 5             # non-interactive (cron-friendly)
  python run_pipeline.py --no-dedupe --count 3        # allow regenerating today's stories
  python run_pipeline.py --no-llm-rank --count 3      # heuristic ranking only (skip LLM rerank)
  python run_pipeline.py --rank-model nvidia-nemotron-ultra --count 1  # Groq scripts + NVIDIA ranking

Valid --model keys: {', '.join(sorted(llm_client.MODEL_REGISTRY.keys()))}
Default model: {llm_client.DEFAULT_MODEL_KEY}
        """,
    )
    parser.add_argument(
        "--count", type=int, default=3,
        help="Number of Shorts to produce (default: 3, max: 10)"
    )
    parser.add_argument(
        "--outdir", type=str, default="output",
        help="Output directory (default: output)"
    )
    parser.add_argument(
        "--no-video", action="store_true",
        help="Skip voice generation and video assembly (scripts only)"
    )
    parser.add_argument(
        "--quick", action="store_true",
        help="Equivalent to --count 1 --no-video (quick script review)"
    )
    parser.add_argument(
        "--model", type=str, default=llm_client.DEFAULT_MODEL_KEY,
        help=f"LLM model key (default: {llm_client.DEFAULT_MODEL_KEY})"
    )
    parser.add_argument(
        "--rank-model", type=str, default=None,
        help="LLM model key for the editorial rerank ONLY (default: same as "
             "--model). Keeps script generation on one provider while ranking "
             "runs on another — e.g. --rank-model nvidia-nemotron-ultra leaves "
             "scripts on Groq but ranks on NVIDIA (separate rate limits)"
    )
    parser.add_argument(
        "--auto", action="store_true",
        help="Don't prompt for story selection; generate top-N automatically"
    )
    parser.add_argument(
        "--no-dedupe", action="store_true",
        help="Don't filter out stories already generated today"
    )
    parser.add_argument(
        "--no-llm-rank", action="store_true",
        help="Skip the LLM editorial rerank; rank by heuristic score only"
    )
    parser.add_argument(
        "--compare-models", type=str, default=None,
        help="Comma-separated model keys to run the SAME story across (e.g. 'groq-gpt-oss-120b,nvidia-nemotron-ultra')"
    )
    parser.add_argument(
        "--with-hook-text", action="store_true",
        help="Burn headline as on-screen text overlay (first 3s) on gradient fallback videos"
    )
    parser.add_argument(
        "--branding", action="store_true",
        help="Enable branding elements (placeholder for future intro/outro)"
    )
    parser.add_argument(
        "--render", action="store_true",
        help="Render video with Remotion after staging assets (default: stage only, use --render to actually render)"
    )

    args = parser.parse_args()

    # Validate model key early
    try:
        llm_client.resolve_model(args.model)
    except ValueError as e:
        print(f"ERROR: {e}")
        sys.exit(1)

    # Rank model defaults to --model (backward compatible); validate early too
    rank_model = args.rank_model or args.model
    try:
        llm_client.resolve_model(rank_model)
    except ValueError as e:
        print(f"ERROR: Invalid --rank-model: {e}")
        sys.exit(1)

    # Handle --compare-models
    compare_models = None
    if args.compare_models:
        compare_models = [m.strip() for m in args.compare_models.split(",")]
        for mk in compare_models:
            try:
                llm_client.resolve_model(mk)
            except ValueError as e:
                print(f"ERROR: Invalid model in --compare-models: {e}")
                sys.exit(1)

    # Handle --quick shortcut
    if args.quick:
        args.count = 1
        args.no_video = True

    # Clamp count
    args.count = max(1, min(args.count, 10))

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    # Check prerequisites (warn about missing API key for chosen model, but don't block)
    if not args.no_video:
        check_prerequisites(args.model)

    print()
    print("╔══════════════════════════════════════════════════════╗")
    print("║   YouTube Shorts AI Agent — Phase 1 Pipeline       ║")
    print("╠══════════════════════════════════════════════════════╣")
    print(f"║  Target: {args.count} Shorts{' (scripts only)' if args.no_video else ''}            ║")
    print(f"║  Model:  {args.model:<46} ║")
    if rank_model != args.model:
        print(f"║  Rank:   {rank_model:<46} ║")
    print(f"║  Output: {outdir.resolve()}  ║")
    print("╚══════════════════════════════════════════════════════╝")
    print()

    # --- Step 1: Discover trending topics ---
    print("┌─ Step 1: Discovering trending topics")
    print("│")
    top_stories = rank_top_stories()  # full heuristic-ranked candidate pool

    if not top_stories:
        print("│  No stories found. Check your network connection / feed URLs.")
        print("└─ Aborting.")
        return

    # --- Daily dedupe (BEFORE the LLM rerank — don't burn picks on stories
    #     that were already generated today) ---
    if not args.no_dedupe:
        top_stories, removed = _filter_deduped_today(outdir, top_stories)
        if removed:
            print(f"│  [dedupe] Filtered out {removed} story(s) already generated today.")
        if not top_stories:
            print("│  All candidates already generated today. Use --no-dedupe to override.")
            print("└─ Aborting.")
            return

    # --- Step 1.5: LLM editorial rerank (precision layer over the pool) ---
    # Heuristic scoring stays as the cheap recall filter; ONE LLM call does
    # the editorial taste ranking and returns a best-first shortlist with a
    # one-line reason per pick. Any failure falls back to heuristic order.
    # Uses --rank-model when set (decoupled from the script-generation model —
    # e.g. Groq's 8k TPM cap rejects the ~8.5k-token rerank request, while
    # NVIDIA's limits absorb it).
    if args.no_llm_rank:
        print("│  [llm-rank] skipped (--no-llm-rank) — heuristic order")
        rank_source = "heuristic"
    else:
        print(f"│  Running LLM editorial rerank ({rank_model})...")
        top_stories, rank_source = llm_ranker.rerank(
            top_stories, model_key=rank_model,
            max_picks=max(args.count * 3, 12),
        )
    print("└─")

    # --- Story picker (interactive) ---
    if not args.auto and sys.stdin.isatty():
        print()
        print("┌─ Story Selection")
        print("│  Enter numbers (e.g. 1,3,5), 'top3', 'all', or press Enter for top-N:")
        print("└─")

        cat_order = ["ai", "business", "science", "general"]
        # picker_list IS the order the displayed numbers refer to — every
        # selection below indexes THIS list, never top_stories directly.
        # (Bug fix: the grouped-by-category display reorders stories, so
        # displayed #N silently mapped to a different story than
        # top_stories[N-1] — typing "3" could generate the story shown as #2.)
        if rank_source == "llm":
            picker_list = list(top_stories)
            # LLM order IS the editorial ranking — show best-first with reasons
            for display_idx, s in enumerate(picker_list, 1):
                print(f"  {display_idx:2d}. [{s.get('score','?'):>5}] ({s['source']}) {s['title'][:75]}")
                if s.get("llm_reason"):
                    print(f"       └─ {s['llm_reason']}")
                if s.get("llm_dup_of"):
                    print(f"       └─ ↻ same event as #{s['llm_dup_of']}")
        else:
            # Heuristic fallback: grouped by category (old behavior)
            by_cat = defaultdict(list)
            for s in top_stories:
                cat = s.get("category", "general")
                by_cat[cat].append(s)

            picker_list = []
            for cat in cat_order:
                if by_cat[cat]:
                    print(f"  ── {cat.upper()} ──")
                    for s in by_cat[cat]:
                        print(f"  {len(picker_list) + 1:2d}. [{s.get('score','?'):>5}] ({s['source']}) {s['title'][:75]}")
                        picker_list.append(s)
        print()
        tries = 0
        selected = []
        while tries < 3:
            try:
                choice = input("Pick stories to generate: ").strip()
            except (EOFError, KeyboardInterrupt):
                choice = ""
            if not choice:
                selected = picker_list[:args.count]
                break
            choice_lower = choice.lower()
            if choice_lower == "all":
                selected = picker_list
                break
            if choice_lower.startswith("top") and choice_lower[3:].isdigit():
                n = int(choice_lower[3:])
                selected = picker_list[:n]
                break
            # Parse comma-separated indices
            try:
                indices = [int(x.strip()) - 1 for x in choice.split(",")]
                selected = [picker_list[i] for i in indices if 0 <= i < len(picker_list)]
                if selected:
                    break
            except (ValueError, IndexError):
                pass
            tries += 1
            print(f"  Invalid input. Try again ({3 - tries} tries left).")
        if not selected:
            print("  Too many invalid attempts. Falling back to top-N.")
            selected = picker_list[:args.count]
        top_stories = selected
    else:
        # Non-interactive: take the ranked best-N. With LLM ranking this is
        # the editorial shortlist order; same-event duplicates (flagged by
        # the reranker) are dropped in favor of their best-ranked keeper.
        selected = []
        for s in top_stories:
            if s.get("llm_dup_of"):
                continue
            selected.append(s)
            if len(selected) >= args.count:
                break
        top_stories = selected

    # --- Steps 2-3: Research + Script ---
    print()
    print("┌─ Steps 2-3: Researching & writing scripts")
    print("│")

    completed = 0
    failed = 0
    total_assets_downloaded = 0
    fallback_count = 0
    total_fetched_chars = 0
    total_comments = 0

    # Determine which models to run
    models_to_run = compare_models if compare_models else [args.model]
    
    # Fallback models if primary fails (Groq models are more reliable).
    # Derived from the registry so it can't go stale when rows are
    # added/removed — Groq keys first, then the rest.
    _all_keys = llm_client.model_keys()
    fallback_models = ([k for k in _all_keys if k.startswith("groq-")]
                       + [k for k in _all_keys if not k.startswith("groq-")])

    for i, story in enumerate(top_stories, 1):
        print(f"│")
        print(f"│  [{i}/{len(top_stories)}] {story['title'][:80]}")

        for model_idx, model_key in enumerate(models_to_run):
            # Try primary model, then fallbacks if it fails
            models_to_try = [model_key] + [m for m in fallback_models if m != model_key]
            
            result = None
            last_error = None
            for try_model in models_to_try:
                try:
                    result = process_story(story, model_key=try_model)
                    if try_model != model_key:
                        print(f"│  ↻ Fell back to {try_model} after {model_key} failed")
                    break
                except Exception as e:
                    last_error = e
                    print(f"│  ⚠ Model {try_model} failed: {e}")
                    continue
            
            if result is None:
                print(f"│  ✗ FAILED (all models exhausted): {last_error}")
                failed += 1
                continue

            try:
                project_dir, assets_got = save_project(result, outdir, i, no_video=args.no_video, model_key=model_key, render_hook_text=args.with_hook_text)

                # Render with Remotion if --render flag is set and not --no-video
                if args.render and not args.no_video and project_dir.exists():
                    try:
                        print(f"│  ─ Rendering video with Remotion...")
                        output_video = assemble_video_remotion(project_dir)
                        print(f"│  ✓ Rendered: {output_video.name}")
                    except Exception as rend_err:
                        print(f"│  ⚠ Remotion render FAILED: {rend_err}")

                # Log to daily dedupe
                _log_generated_story(outdir, story, model_key, project_dir.name)
                completed += 1
                total_assets_downloaded += assets_got

                # Track fallback metrics
                fetch = result.get("research", {})
                if fetch.get("used_fallback"):
                    fallback_count += 1
                total_fetched_chars += fetch.get("article_chars", 0)
                total_comments += fetch.get("comment_count", 0)

            except Exception as e:
                print(f"│  ✗ FAILED (model: {model_key}): {e}"); import traceback; traceback.print_exc()
                failed += 1
                continue

    # --- Summary ---
    print()
    daily_dir = _get_daily_outdir(outdir)
    print("╔══════════════════════════════════════════════════════╗")
    print(f"║  Done: {completed} successful, {failed} failed                   ║")
    rank_label = "LLM editorial rerank" if rank_source == "llm" else "heuristic scores"
    print(f"║  Ranking: {rank_label}  ║")
    print(f"║  Output: {daily_dir.resolve()}  ║")
    if not args.no_video and completed > 0:
        print("║                                                      ║")
        print("║  Each project folder contains:                        ║")
        print("║  ├── script.txt         Narration script              ║")
        print("║  ├── narration.mp3      Voiceover audio               ║")
        print("║  ├── storyboard.md      Visual shot plan              ║")
        print("║  ├── captions.srt       Timed subtitles               ║")
        print("║  ├── metadata.txt       Title, source, score          ║")
        print("║  ├── research_notes.json Fact-checked research         ║")
        print("║  ├── asset_plan.json    Keywords + stock search       ║")
        print("║  ├── thumbnail_notes.txt Thumbnail design suggestions ║")
        print("║  ├── edit_plan.json     Full editing instructions     ║")
        print("║  ├── youtube_meta.json  YouTube title + description   ║")
        print("║  ├── remotion_props.json Remotion props              ║")
        print("║  └── assets/            Staged assets (public/project_assets/) ║")
        print(f"║  Assets downloaded: {total_assets_downloaded} total              ║")
        render_status = "rendered" if args.render else "render skipped"
        print(f"║  ── Assets staged to public/ (Remotion {render_status}) ║")
        if total_assets_downloaded == 0:
            print("║  ⚠ WARNING: No stock assets downloaded — videos used gradient fallback  ║")
        if fallback_count > 0:
            avg_chars = total_fetched_chars // completed if completed else 0
            print(f"║  Article fetch: {completed - fallback_count}/{completed} full, {fallback_count} fallback  ║")
            print(f"║  Avg article chars: {avg_chars}, total comments: {total_comments}  ║")
    elif args.no_video and completed > 0:
        print("║  (--no-video: scripts only — no audio or video)       ║")
        if fallback_count > 0:
            avg_chars = total_fetched_chars // completed if completed else 0
            print(f"║  Article fetch: {completed - fallback_count}/{completed} full, {fallback_count} fallback  ║")
            print(f"║  Avg article chars: {avg_chars}, total comments: {total_comments}  ║")
    print("╚══════════════════════════════════════════════════════╝")
    print()


if __name__ == "__main__":
    main()
