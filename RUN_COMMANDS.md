# YouTube Shorts AI Agent — Complete Run Command Reference

This document lists **every possible way to run the pipeline** with explanations, examples, and tips.

---

## Quick Reference Table

| Flag | Purpose | Default |
|------|---------|---------|
| `--count N` | Number of Shorts to produce (1–10) | 3 |
| `--outdir DIR` | Output directory | `output` |
| `--no-video` | Scripts + voice only (skip asset download & video assembly) | off |
| `--quick` | Shortcut: 1 script only, no video | off |
| `--model KEY` | LLM model to use | `groq-llama33` |
| `--auto` | Non-interactive: pick top-N automatically (cron-friendly) | off |
| `--no-dedupe` | Allow regenerating stories already done today | off |

---

## Available Models (`--model`)

Run `python llm_client.py --list` to see current registry. As of this writing:

| Key | Provider | Model ID | Notes |
|-----|----------|----------|-------|
| `groq-llama33` | Groq | `llama-3.3-70b-versatile` | **Default** — fast, free, 30 req/min |
| `groq-deepseek` | Groq | `deepseek-r1-distill-llama-70b` | Reasoning-heavy |
| `groq-llama4` | Groq | `meta-llama/llama-4-scout-17b-16e-instruct` | Llama 4 Scout |
| `nvidia-nemotron-super` | NVIDIA | `nvidia/llama-3.3-nemotron-super-49b-v1.5` | Nemotron Super 49B v1.5 |
| `nvidia-llama33` | NVIDIA | `meta/llama-3.3-70b-instruct` | Llama 3.3 on NVIDIA |
| `nvidia-gpt-oss-120b` | NVIDIA | `openai/gpt-oss-120b` | **NEW** — OpenAI GPT-OSS 120B, strong reasoning |
| `nvidia-nemotron-super-49b-v15` | NVIDIA | `nvidia/llama-3.3-nemotron-super-49b-v1.5` | **NEW** — explicit v1.5 key |
| `nvidia-llama-3-1-70b` | NVIDIA | `meta/llama-3.1-70b-instruct` | **NEW** — Llama 3.1 70B Instruct |

---

## Common Run Commands

### 1. First-time / Quick Test (no video)
```bash
python run_pipeline.py --quick
```
- Produces **1 script only** (no voice, no video)
- Takes ~30–60 seconds
- Great for verifying API keys and prompt quality

### 2. Single Full Video (end-to-end)
```bash
python run_pipeline.py --count 1
```
- 1 complete Short: script → voice → assets → `draft_video.mp4`
- Takes ~5–10 minutes (depends on Pexels download speed)
- Output in `output/01_<slug>/`

### 3. Multiple Videos (batch)
```bash
python run_pipeline.py --count 5
```
- 5 complete Shorts
- Interactive story picker (unless `--auto`)

### 4. Scripts Only (no video assembly)
```bash
python run_pipeline.py --count 3 --no-video
```
- Generates scripts, research, voice narration — **no** asset download or video assembly
- Much faster, useful for reviewing scripts before committing to video render

### 5. Use a Different Model
```bash
# Groq DeepSeek (reasoning)
python run_pipeline.py --model groq-deepseek --count 1

# NVIDIA Nemotron Super (reasoning)
python run_pipeline.py --model nvidia-nemotron-super --count 1

# NEW: OpenAI GPT-OSS 120B on NVIDIA
python run_pipeline.py --model nvidia-gpt-oss-120b --count 1

# NEW: Nemotron Super 49B v1.5 (explicit key)
python run_pipeline.py --model nvidia-nemotron-super-49b-v15 --count 1

# NEW: Llama 3.1 70B on NVIDIA
python run_pipeline.py --model nvidia-llama-3-1-70b --count 1
```

### 6. Non-Interactive / Cron-Friendly
```bash
python run_pipeline.py --auto --count 5
```
- **No prompts** — automatically takes top-N stories
- Perfect for cron jobs, CI/CD, scheduled runs
- Combine with `--no-video` for script-only cron:
```bash
python run_pipeline.py --auto --count 10 --no-video
```

### 7. Override Daily Deduplication
```bash
python run_pipeline.py --no-dedupe --count 3
```
- By default, stories already generated **today** are filtered out (logged in `output/_generated_log.json`)
- Use this to re-generate a story you weren't happy with

### 8. Custom Output Directory
```bash
python run_pipeline.py --count 3 --outdir my_shorts
```
- Output goes to `my_shorts/` instead of `output/`

### 9. Combine Flags
```bash
# 5 videos, DeepSeek, non-interactive, custom folder
python run_pipeline.py --model groq-deepseek --count 5 --auto --outdir batch_001

# 1 video, GPT-OSS 120B, no dedupe (re-run today's story)
python run_pipeline.py --model nvidia-gpt-oss-120b --count 1 --no-dedupe

# 10 scripts only for review, auto-pick, custom dir
python run_pipeline.py --count 10 --no-video --auto --outdir script_review
```

---

## Story Selection (Interactive Mode)

When you run **without `--auto`** and have a TTY, you'll see:

```
┌─ Story Selection
│  Enter numbers (e.g. 1,3,5), 'all', 'top3', or press Enter for top-N:
│  1. [  85] (hackernews) OpenAI releases GPT-5 with 10x reasoning
│  2. [  72] (rss) NVIDIA announces new Blackwell GPU architecture
│  3. [  68] (reddit) Microsoft's Phi-4 beats GPT-4 on benchmarks
│  ... and 12 more (use 'all' to see them)
└─
Pick stories to generate:
```

**Valid inputs:**
| Input | Meaning |
|-------|---------|
| (empty / Enter) | Take top-N (where N = `--count`) |
| `1,3,5` | Pick stories 1, 3, and 5 |
| `all` | Generate ALL fetched candidates |
| `top3` | Top 3 (also `top5`, `top10`, etc.) |

**Tip:** If the picker feels "not smooth," check:
- Are you in a real TTY? (Some terminals/IDEs don't allocate one)
- Use `--auto` to bypass entirely for automation
- The prompt accepts comma-separated indices — no spaces needed (`1,3,5` not `1, 3, 5`)

---

## Understanding the Output Structure

Each Short gets its own folder: `output/NN_<slug>/`

```
output/01_openai_releases_gpt5/
├── script.txt              # Narration script (6–9 sentences, 110–150 words)
├── research_notes.json     # Fact-checked research from article + comments
├── metadata.json           # Title, source, link, score, fetch quality
├── narration.mp3           # Edge-TTS voiceover (en-US-AndrewNeural, +20% rate)
├── headline.txt            # On-screen hook headline
├── storyboard.md           # Per-sentence visual plan
├── captions.srt            # Timed subtitles (from REAL audio duration)
├── asset_plan.json         # Keywords + per-sentence Pexels search terms
├── timing.json             # Sentence start/end times (from ffprobe)
├── thumbnail_notes.txt     # Thumbnail design suggestions
├── edit_plan.json          # Full editing instructions for DaVinci/CapCut
├── youtube_meta.json       # YouTube title (≤60 chars), description (≤200 + tags)
├── edit_plan.json          # Editing instructions
├── assets/                 # Per-sentence clips (video or photo)
│   ├── 0.mp4
│   ├── 1.jpg
│   └── ...
└── draft_video.mp4         # Assembled 1080×1920 Short (no burned captions)
```

**Key files to review:**
- `script.txt` — Read the narration; tweak if needed before video
- `youtube_meta.json` — Ready-to-copy title + description + hashtags
- `draft_video.mp4` — The assembled draft (open in any player)
- `asset_plan.json` — See what search terms were used per sentence

---

## Cache Behavior (Pexels Clips)

The pipeline caches downloaded clips at `~/.shorts_clip_cache.json`:
- **Video clips** and **photos** are cached by search term
- If you re-run with similar topics, clips are **reused** (saves bandwidth)
- Cache is global across all runs/projects
- To force fresh downloads, delete the cache file:
```bash
rm ~/.shorts_clip_cache.json
```

**Note:** During asset download, you'll see messages like:
```
  ─ Downloading 7 per-sentence asset(s) from Pexels...
  [cache hit] "AI chip" -> /home/user/.cache/shorts_clips/ai_chip_abc123.mp4
  [downloaded] "neural network" -> assets/1.mp4 (2.3 MB)
```
This is normal — cache hits are fast, downloads take a few seconds each.

---

## Prerequisites Checklist

Before running, ensure:

1. **`.env` file exists** in project root with:
   ```
   GROQ_API_KEY="gsk-..."       # REQUIRED for script generation
   PEXELS_API_KEY="qkn-..."     # REQUIRED for video/photo assets
   NVIDIA_API_KEY="nvapi-..."   # OPTIONAL — for NVIDIA models
   ```

2. **System tools installed:**
   ```bash
   # Ubuntu/Debian
   sudo apt install ffmpeg curl
   
   # macOS
   brew install ffmpeg curl
   ```

3. **Python deps:**
   ```bash
   pip install -r requirements.txt
   # (requests, edge-tts, Pillow, trafilatura, readability-lxml)
   ```

4. **Run the prerequisites check:**
   ```bash
   python run_pipeline.py --quick  # Will warn if keys missing
   ```

---

## Troubleshooting Common Issues

| Symptom | Likely Cause | Fix |
|---------|--------------|-----|
| `GROQ_API_KEY is not set` | Missing/invalid key in `.env` | Add valid key from console.groq.com |
| `PEXELS_API_KEY is not set` | Missing Pexels key | Add key from pexels.com/api |
| `curl returned 28` / timeout | LLM call took too long | Model may be slow; try `groq-llama33` |
| `HTTP 403` from Pexels | Missing browser User-Agent | Fixed in code — update `asset_collector.py` |
| `No stories found` | Network / feed issue | Check internet; RSS feeds may be down |
| `draft_video.mp4` is 1080×1920 but no video track | Pexels download failed | Check `PEXELS_API_KEY`; delete cache and retry |
| Story picker doesn't appear | Not in a TTY (e.g., IDE terminal) | Use `--auto` or run in real terminal |

---

## Advanced: Running Individual Modules for Debugging

Each module has a `__main__` block for standalone testing:

```bash
# 1. News fetcher — see top stories with scores
python news_fetcher.py

# 2. Article fetcher — fetch full article + comments for a URL
python article_fetcher.py "https://news.ycombinator.com/item?id=12345"
# Or with no args: demos on current top HN story

# 3. LLM client — list models or smoke-test
python llm_client.py --list
python llm_client.py --model groq-llama33

# 4. Script generator — full combined call on a URL
python script_generator.py "https://example.com/article"

# 5. Voice generator — test TTS
python voice_generator.py --script "Hello world" --output /tmp/test.mp3

# 6. Storyboard generator — reads stdin, writes test_output/
python storyboard_generator.py

# 7. Asset collector — legacy (flat keywords) or per-sentence plan
python asset_collector.py "technology" /tmp/test_cache
# Or build a plan and call collect_assets_for_plan()

# 8. Video assembler — needs a project with narration + assets
python video_assembler.py output/01_test_project

# 9. Full quick test (1 script, no video)
python run_pipeline.py --quick

# 10. Full video test (1 complete Short)
python run_pipeline.py --count 1
```

---

## Environment Variables Summary

| Variable | Required? | Where to Get |
|----------|-----------|--------------|
| `GROQ_API_KEY` | **Yes** (for Groq models) | https://console.groq.com |
| `NVIDIA_API_KEY` | **Yes** (for NVIDIA models) | https://build.nvidia.com |
| `PEXELS_API_KEY` | **Yes** (for video/photos) | https://pexels.com/api |

All three can be in `.env` file (auto-loaded) or exported in shell.

---

## Model Selection Guide

| Use Case | Recommended Model |
|----------|-------------------|
| **Default / general** | `groq-llama33` (fast, free, reliable) |
| **Complex reasoning / technical depth** | `groq-deepseek` or `nvidia-nemotron-super` |
| **Maximum reasoning capacity** | `nvidia-gpt-oss-120b` (120B params) |
| **Balanced quality/speed on NVIDIA** | `nvidia-llama33` or `nvidia-llama-3-1-70b` |
| **Testing / experimentation** | Try all — each has different "voice" |

---

## Cron Example (Daily Automated Run)

```bash
# /etc/cron.d/shorts-pipeline (or crontab -e)
# Runs at 6 AM daily: generates 3 Shorts non-interactively
0 6 * * * cd /path/to/video_maker && /usr/bin/python3 run_pipeline.py --auto --count 3 --model groq-llama33 >> /var/log/shorts_pipeline.log 2>&1
```

**Tip:** Use `--no-video` for a lighter cron that only produces scripts:
```bash
0 6 * * * cd /path/to/video_maker && /usr/bin/python3 run_pipeline.py --auto --count 10 --no-video --model groq-llama33
```

---

## Need Help?

Run any command with `--help`:
```bash
python run_pipeline.py --help
python llm_client.py --help
python script_generator.py --help
```

Or check the main docs: `CLAUDE.md` (project architecture) and `pipeline_upgrade_spec.md` (upgrade history).