# CLAUDE.md — YouTube Shorts AI Agent

Project guide for the automated, zero-cost pipeline turning trending tech/AI/business news into ready-to-edit YouTube Shorts (9:16, 1080×1920).

---

## 1. Quick Start & Environment

- **Runtime:** Python 3.13+ (always run scripts with `python3`, never bare `python`).
- **Dependencies:** `python3 -m pip install --break-system-packages -r requirements.txt` (`requests`, `edge-tts`, `Pillow`, `trafilatura`, `readability-lxml`, `youtube-transcript-api`).
- **System Tools:** `ffmpeg`, `ffprobe`, `curl`.
- **Environment (`.env`):**
  ```env
  GROQ_API_KEY="gsk-..."      # Script generation & Groq LLM calls
  PEXELS_API_KEY="qkn-..."    # Stock footage/photo downloads
  NVIDIA_API_KEY="nvapi-..."  # Optional: NVIDIA NIM alternative models
  ```

### Common Commands
```bash
python3 run_pipeline.py --count 3 --outdir output      # Full run (default 3 stories)
python3 run_pipeline.py --quick                       # 1 script only, no video
python3 run_pipeline.py --count 1 --no-video          # Scripts + voice only
python3 run_pipeline.py --auto --count 5              # Non-interactive (cron)
python3 run_pipeline.py --model nvidia-nemotron-ultra # Use NVIDIA NIM
python3 run_pipeline.py --rank-model nvidia-nemotron-ultra # Groq scripts + NVIDIA ranking
python3 llm_client.py --bench                         # Benchmark all models
```

---

## 2. Pipeline Architecture & Flow

```
run_pipeline.py (Orchestrator)
  ├─ 1. news_fetcher.rank_top_stories() → fetch RSS (blogs, CNBC, TechCrunch, Reddit, Google News) + HN + YouTube feeds, score & rank pool (40 candidates)
  ├─ 2. Daily dedupe (output/<daily>/_generated_log.json)
  ├─ 3. llm_ranker.rerank() → LLM editorial rerank (best-first shortlist + reasons + duplicates; falls back to heuristic on error)
  ├─ 4. Interactive Picker (unless --auto) → user selects stories
  └─ For each selected story:
       ├─ article_fetcher.fetch_article_content() → scrape article/transcripts + comments (HN/Reddit fallback to RSS summary)
       ├─ script_generator.generate_combined() → ONE LLM call: script (6-9 sentences, 110-150w), headline, youtube_title (≤60c), youtube_description (≤200c), shots[] plan
       ├─ voice_generator.generate_narration() → narration.mp3 (edge-tts +20% rate)
       ├─ storyboard_generator.generate_storyboard() → storyboard.md, captions.srt, asset_plan.json, timing.json
       ├─ asset_collector.collect_assets_for_plan() → Pexels assets (one per sentence, video/photo, LLM tag verification, 2-use limit, max 15MB)
       └─ video_assembler.assemble_video_simple() → draft_video.mp4 (ffmpeg, chronological clip-per-sentence, xfade 0.2s transitions, Ken-Burns zoompan on photos, 1000Hz sentence-boundary clicks, no burned captions)
```

---

## 3. Core Modules & Contracts

- **`run_pipeline.py`**: Orchestrates fetching, ranking, picking, project generation, and video assembly. Handles retries and asset re-downloads if clips exceed reuse limits.
- **`llm_client.py`**: Provider-agnostic LLM transport for Groq and NVIDIA NIM (`call_llm` via curl, MODEL_REGISTRY, error guards, token/time scaling, `--bench`).
- **`news_fetcher.py`**: Discovers stories from RSS, HN, Google News, and YouTube channel feeds; computes recency + niche + engagement score.
- **`llm_ranker.py`**: Precision editorial reranker over candidate pool. Outputs best-first order with reasons.
- **`article_fetcher.py`**: Extracts article text (trafilatura → readability → stdlib) and top comments. Routes YouTube URLs to `youtube-transcript-api`.
- **`script_generator.py`**: Combined LLM call producing script, metadata, and per-sentence visual plan with strict validation.
- **`voice_generator.py`**: Generates high-speed Microsoft Edge TTS narration (`en-US-AndrewNeural`).
- **`storyboard_generator.py`**: Formats the combined LLM shot plan into SRT, timing files, and storyboard markdown without extra LLM calls.
- **`asset_collector.py`**: Downloads Pexels videos/photos with LLM relevance tag verification and caching.
- **`video_assembler.py`**: FFmpeg assembly pipeline enforcing hard max durations (3.0s video, 1.5s photo), xfade transitions, audio padding, and subtle transition clicks.

---

## 4. Key Gotchas & Troubleshooting

1. **API Keys:** Check `GROQ_API_KEY` and `PEXELS_API_KEY` in `.env`.
2. **Groq TPM Limits:** Full rerank can hit Groq's 8k TPM limit; use `--rank-model nvidia-nemotron-ultra` to offload ranking.
3. **Execution:** Always use `python3`, never `python`.
4. **Module Tests:** Each module has a `if __name__ == "__main__"` smoke test (e.g., `python3 llm_client.py --list`).
