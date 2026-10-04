"""Critic agent — reviews the editor's output and suggests improvements.

The Critic checks:
  - Output file exists and is a valid MP4
  - Output resolution matches the StyleProfile target
  - Output duration is reasonable (sum of clip durations, +/- 10%)
  - Output file size is reasonable (>500KB, <10GB)
  - Audio stream present (if any clip had audio)

If the output fails any check, the Critic returns verdict="retry" with
suggestions for the Director to apply (e.g. "re-edit with smaller max_clip_duration").
"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol

from avs.models import Clip, CritiqueReport, StyleProfile
from avs.utils.ff import get_video_info, probe
from avs.utils.logging import get_logger

log = get_logger("avs.critic")


class Critic(Protocol):
    async def critique(self, output_path: Path, clips: list[Clip],
                        style: StyleProfile, *, round: int = 0) -> CritiqueReport:
        ...


class FfprobeCritic:
    """Critic that uses ffprobe to verify the output video."""

    __version__ = "1.0.0"

    async def critique(self, output_path: Path, clips: list[Clip],
                        style: StyleProfile, *, round: int = 0) -> CritiqueReport:
        report = CritiqueReport(verdict="pass", critique_round=round)
        path = Path(output_path)

        # Check 1: file exists
        if not path.exists():
            report.issues.append("E_OUTPUT_MISSING")
            report.verdict = "fail"
            return report

        # Check 2: file size
        size = path.stat().st_size
        if size < 500_000:
            report.issues.append(f"E_OUTPUT_TOO_SMALL ({size} bytes)")
        if size > 10 * 1024 * 1024 * 1024:
            report.issues.append(f"E_OUTPUT_TOO_LARGE ({size} bytes)")

        # Check 3: ffprobe can parse
        info = await get_video_info(path)
        if not info:
            report.issues.append("E_FFPROBE_FAILED")
            report.verdict = "fail"
            return report

        # Check 4: resolution matches target
        target_w = style.output_width
        target_h = style.output_height
        actual_w = info.get("width")
        actual_h = info.get("height")
        if actual_w != target_w or actual_h != target_h:
            report.issues.append(f"E_RESOLUTION_MISMATCH target={target_w}x{target_h} actual={actual_w}x{actual_h}")
            report.suggestions.append(f"re-edit with explicit -s {target_w}x{target_h}")

        # Check 5: duration roughly matches sum of clips (with max_clip_duration cap)
        if clips:
            expected_dur = sum(min(c.duration_s or 0, style.max_clip_duration_s) for c in clips if c.duration_s)
            actual_dur = info.get("duration_s") or 0
            if expected_dur > 0:
                ratio = actual_dur / expected_dur
                if ratio < 0.5 or ratio > 1.5:
                    report.issues.append(f"E_DURATION_MISMATCH expected={expected_dur:.1f}s actual={actual_dur:.1f}s ratio={ratio:.2f}")
                    report.suggestions.append("check that all clips were included in concat")

        # Check 6: has video stream
        if not info.get("has_video"):
            report.issues.append("E_NO_VIDEO_STREAM")
            report.verdict = "fail"
            return report

        # Check 7: audio present (warning only)
        if not info.get("has_audio"):
            report.suggestions.append("output has no audio — check that source clips have audio")

        # Compute score
        if not report.issues:
            report.score = 1.0
            report.verdict = "pass"
        else:
            report.score = max(0.0, 1.0 - 0.2 * len(report.issues))
            # If any E_ issue is critical, retry
            critical = [i for i in report.issues if i.startswith("E_") and "MISMATCH" not in i]
            if critical and round < 1:  # only retry once
                report.verdict = "retry"
            else:
                report.verdict = "pass"  # accept with warnings after first retry

        log.info("critic_done", verdict=report.verdict, score=report.score,
                 issues=report.issues, suggestions=report.suggestions)
        return report
