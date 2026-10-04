# CLAUDE.md — Project Memory for AI Agents

> Read this file first, before reading any other doc or writing any code.

---

## 0. Identity

**Project name:** `agent-video-studio` (codename `avs`)
**Repo:** https://github.com/hamza140202/agent-video-studio
**Version:** 1.0.0
**Purpose:** A multi-agent system that turns a content brief + source TikTok URLs into a publish-ready YouTube compilation video (16:9, blurred background, centered vertical source, hard cuts, original audio preserved). Built for AI agents (Claude, GLM, Cursor, Cline) that need to produce a final video autonomously.

**Guiding philosophy:** *"Perfection is the key."* The Director must not return a result until the Critic has passed. If the Critic returns `verdict="retry"`, the Director applies suggestions and re-runs the Editor (max 1 retry).

---

## 1. Operating environment

- Cloud Linux VM with ffmpeg/ffprobe installed
- `avd` (agent-video-downloader) installed as a dependency — used by the Researcher to bulk-download source clips
- Python 3.10+
- No browser, no cookies, no login sessions

---

## 2. Architecture at a glance

```
Brief + URLs.txt
  │
  ▼
Director ───┬── Researcher (avd batch download)
            ├── StyleAnalyst (reference or template)
            ├── Editor (ffmpeg filter_complex: blurred bg + centered fg)
            ├── Critic (verify resolution/duration/audio)
            └── Publisher (final H.264 encode with faststart)
  │
  ▼
final.mp4 (1920x1080, YouTube-ready)
```

The Director is the only agent callers invoke directly. The other 5 are sub-agents invoked by the Director.

---

## 3. Agents

| Agent | File | Role |
|---|---|---|
| `Director` | `src/avs/director.py` | Orchestrates the pipeline, handles Critic retries |
| `Researcher` | `src/avs/agents/researcher.py` | Bulk-downloads source clips via `avd batch` |
| `StyleAnalyst` | `src/avs/agents/style_analyst.py` | Analyzes reference video OR uses documented template |
| `Editor` | `src/avs/agents/editor.py` | Composes final video (ffmpeg filter_complex) |
| `Critic` | `src/avs/agents/critic.py` | Verifies output (resolution, duration, audio, file size) |
| `Publisher` | `src/avs/agents/publisher.py` | Final high-quality H.264 encode with faststart |

Each agent is a plain Python class with a `typing.Protocol` interface. No LLM in the runtime loop — determinism and replayability are preferred.

---

## 4. Style profile (the editing style)

The StyleAnalyst produces a `StyleProfile` that drives the Editor. Default template:
- `output_width=1920, output_height=1080, output_fps=30, output_crf=18`
- `background_mode="blurred_source"`, `background_blur_sigma=20`, `background_brightness=0.5`
- `cut_mode="hard"`, `max_clip_duration_s=30.0`
- `title_overlay=brief[:80]` (the content brief truncated)
- `audio_mode="original"` (preserve original audio from each clip)

If a reference video is provided, the StyleAnalyst probes it and adopts its resolution if it's 16:9 horizontal.

---

## 5. Quickstart (for an agent that just landed in this repo)

```bash
git clone https://github.com/hamza140202/agent-video-studio
cd agent-video-studio
pip install -e .
avd agent-setup   # one-time bootstrap

# Create urls.txt with one TikTok URL per line
echo "https://www.tiktok.com/@anyuser/video/1234567890" > urls.txt

# Produce the final compilation
avs run "Best of Ahyeon September TikTok" --urls-file urls.txt
```

---

## 6. Coding doctrine (non-negotiable)

1. **Perfection is the key.** The Director must not return until the Critic passes.
2. **No LLM in runtime loop.** Agents are deterministic Python classes.
3. **ffmpeg for everything.** No moviepy, no opencv — just ffmpeg subprocess calls.
4. **`avd` is the only downloader.** The Researcher uses `avd batch` to download source clips.
5. **Honest negatives are first-class.** If no clips download, return `status="failed"` with structured error — never raise.
6. **Logs on stderr, data on stdout.** Always pipe-safe.
7. **Atomic writes.** The Publisher writes to a `.part` file and `os.rename` only after success.
8. **The Critic retries once.** If verdict="retry", the Director applies suggestions and re-runs Editor + Critic. After 1 retry, accept with warnings.

---

## 7. Source of truth documents

1. `CLAUDE.md` (this file) — read first
2. `README.md` — public-facing
3. `src/avs/models.py` — pydantic data models (the contract)
4. `src/avs/agents/*.py` — agent Protocol interfaces + implementations
5. `src/avs/director.py` — orchestration logic
6. `src/avs/cli.py` — CLI entry point

---

*This file is the entry point. Everything else flows from here.*
