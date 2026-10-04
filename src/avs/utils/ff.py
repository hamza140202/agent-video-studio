"""FFmpeg / FFprobe wrappers for video analysis and editing."""
from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

_FFPROBE = shutil.which("ffprobe") or "ffprobe"
_FFMPEG = shutil.which("ffmpeg") or "ffmpeg"


def is_available() -> bool:
    return shutil.which("ffprobe") is not None and shutil.which("ffmpeg") is not None


async def probe(path: str | Path) -> dict[str, Any] | None:
    """Run ffprobe and return parsed JSON, or None on failure."""
    cmd = [_FFPROBE, "-v", "error", "-show_format", "-show_streams",
           "-print_format", "json", str(path)]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        stdout, _ = await proc.communicate()
        if proc.returncode != 0:
            return None
        return json.loads(stdout.decode(errors="replace"))
    except Exception:
        return None


def extract_probe_summary(probe_json: dict[str, Any]) -> dict[str, Any]:
    """Extract width/height/duration/codec from ffprobe JSON."""
    out = {
        "width": None, "height": None, "duration_s": None,
        "codec_video": None, "codec_audio": None,
        "has_video": False, "has_audio": False,
        "aspect_ratio": "unknown",
    }
    fmt = probe_json.get("format") or {}
    try:
        if fmt.get("duration"):
            out["duration_s"] = float(fmt["duration"])
    except (TypeError, ValueError):
        pass
    for s in probe_json.get("streams") or []:
        if s.get("codec_type") == "video" and not out["has_video"]:
            out["has_video"] = True
            out["width"] = s.get("width")
            out["height"] = s.get("height")
            out["codec_video"] = s.get("codec_name")
            dar = s.get("display_aspect_ratio")
            if dar:
                out["aspect_ratio"] = dar
        elif s.get("codec_type") == "audio" and not out["has_audio"]:
            out["has_audio"] = True
            out["codec_audio"] = s.get("codec_name")
    # Determine if vertical or horizontal
    if out["width"] and out["height"]:
        if out["height"] > out["width"]:
            out["aspect_orientation"] = "vertical"
        elif out["width"] > out["height"]:
            out["aspect_orientation"] = "horizontal"
        else:
            out["aspect_orientation"] = "square"
    else:
        out["aspect_orientation"] = "unknown"
    return out


async def get_video_info(path: str | Path) -> dict[str, Any]:
    """Combined probe + summary. Returns {} on failure."""
    pj = await probe(path)
    if pj is None:
        return {}
    return extract_probe_summary(pj)


async def run_ffmpeg(args: list[str], timeout: int = 600) -> tuple[bool, str]:
    """Run ffmpeg with args (no leading 'ffmpeg'). Returns (ok, stderr_text)."""
    cmd = [_FFMPEG, "-y", "-hide_banner", "-loglevel", "error"] + args
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            return False, stderr.decode(errors="replace")
        return True, ""
    except asyncio.TimeoutExpired:
        return False, f"timeout after {timeout}s"
    except Exception as e:
        return False, f"exception: {e}"


def parse_time_to_seconds(s: str) -> float:
    """Parse HH:MM:SS or MM:SS or SSS.SS to seconds."""
    if not s:
        return 0.0
    parts = s.split(":")
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    elif len(parts) == 2:
        return int(parts[0]) * 60 + float(parts[1])
    return float(s)
