# agent-video-studio (avs)

> **Multi-agent system that turns a content brief + source TikTok URLs into a publish-ready YouTube compilation video.** 16:9, blurred background, centered vertical source, hard cuts, original audio preserved. Built for AI agents (Claude, Cursor, Cline, GLM, GPT) that need to produce a final video autonomously.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)

---

## Quickstart

```bash
pip install agent-video-studio
avd agent-setup   # one-time bootstrap (installs ffmpeg + XHS-Downloader)

# Create urls.txt with one TikTok URL per line
echo "https://www.tiktok.com/@anyuser/video/1234567890" > urls.txt

avs run "Best of Ahyeon September TikTok" --urls-file urls.txt
# → ./output/final.mp4 (1920x1080, H.264, faststart, ready for YouTube upload)
```

For the full agent usage guide:
```bash
avs agent-instructions
```

---

## What it does

Given:
- A **content brief** (e.g. "Best of Ahyeon September TikTok")
- A **URLs file** (one TikTok URL per line)
- Optional: a **reference video** for style analysis

The system produces a final YouTube-ready MP4 with:
- **1920x1080** (16:9 Full HD)
- Source vertical videos **centered**, scaled to fit frame height
- **Blurred background** (the same video scaled + gaussian blurred + darkened)
- **Hard cuts** between clips
- Optional **title overlay** at top
- **Original audio** preserved
- **H.264 CRF 18** (high quality), AAC 192kbps, faststart for web streaming

---

## Architecture (6 plain-Python agents, no LLM in runtime loop)

```
Brief + URLs.txt
  │
  ▼
Director ───┬── Researcher (bulk-downloads source clips via `avd batch`)
            ├── StyleAnalyst (analyzes reference OR uses documented template)
            ├── Editor (ffmpeg filter_complex: blurred bg + centered fg + concat)
            ├── Critic (verifies resolution / duration / audio / file size)
            └── Publisher (final high-quality H.264 encode with faststart)
  │
  ▼
final.mp4 (1920x1080, YouTube-ready)
```

If the Critic returns verdict="retry", the Director applies the suggestions and re-runs the Editor + Critic (max 1 retry).

---

## Commands

| Command | Purpose |
|---|---|
| `avs run "<brief>" --urls-file urls.txt` | Produce the final compilation video |
| `avs run "<brief>" --urls-file urls.txt --reference ref.mp4` | Use a reference video for style analysis |
| `avs run "<brief>" --urls-file urls.txt --json` | Output JSON result to stdout |
| `avs agent-instructions` | Print step-by-step usage guide for AI agents |
| `avs agents` | Show agent versions |

---

## Install

### From PyPI (recommended)

```bash
pip install agent-video-studio
avd agent-setup   # one-time bootstrap
```

### From source

```bash
git clone https://github.com/hamza140202/agent-video-studio
cd agent-video-studio
pip install -e .
```

### System requirements

- Python 3.10+ (tested on 3.10, 3.11, 3.12, 3.13)
- `ffmpeg` / `ffprobe` (auto-installed by `avd agent-setup`)
- `avd` (agent-video-downloader) — installed as a dependency

---

## Configuration

None — avs is config-free. The style is determined by the StyleAnalyst (reference video if provided, documented template otherwise).

---

## Documentation

- `README.md` — this file (install + quickstart + commands)
- `CLAUDE.md` — project memory (read first if modifying)
- `docs/research-blog.md` — full research narrative (TBD)
- Run `avs agent-instructions` for the 8-step agent usage guide

---

## License

MIT. See [LICENSE](LICENSE).

## Repo

- GitHub: https://github.com/hamza140202/agent-video-studio
- PyPI: https://pypi.org/project/agent-video-studio/ (TBD)
- Author: ansaribilal1402
