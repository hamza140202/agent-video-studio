"""Publisher agent — final render and packaging.

After the Critic passes the edited video, the Publisher:
  1. Re-encodes at the highest quality (CRF 18 for H.264, or use the existing file if already good)
  2. Sets faststart for web streaming
  3. Verifies the final output
  4. Returns the publish-ready MP4 path
"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol

from avs.models import StyleProfile
from avs.utils.ff import get_video_info, run_ffmpeg
from avs.utils.logging import get_logger

log = get_logger("avs.publisher")


class Publisher(Protocol):
    async def publish(self, input_path: Path, output_path: Path,
                       style: StyleProfile) -> tuple[bool, str, dict]:
        """Returns (ok, error, info_dict)."""
        ...


class FFmpegPublisher:
    """Publisher that does a final high-quality H.264 encode."""

    __version__ = "1.0.0"

    async def publish(self, input_path: Path, output_path: Path,
                       style: StyleProfile) -> tuple[bool, str, dict]:
        input_path = Path(input_path)
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Probe input
        in_info = await get_video_info(input_path)
        if not in_info:
            return False, "input probe failed", {}

        # If input already meets target resolution + has faststart, just copy
        # (avoid re-encoding if possible)
        if (in_info.get("width") == style.output_width
                and in_info.get("height") == style.output_height):
            log.info("publisher_copy", reason="input already at target resolution")
            # Still re-encode to ensure faststart
            args = [
                "-i", str(input_path),
                "-c:v", "libx264", "-preset", "medium", "-crf", str(style.output_crf),
                "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "192k",
                "-movflags", "+faststart",
                str(output_path)
            ]
        else:
            log.info("publisher_reencode", reason="input not at target resolution")
            # Re-encode with scale
            args = [
                "-i", str(input_path),
                "-vf", f"scale={style.output_width}:{style.output_height}",
                "-c:v", "libx264", "-preset", "medium", "-crf", str(style.output_crf),
                "-pix_fmt", "yuv420p",
                "-r", str(style.output_fps),
                "-c:a", "aac", "-b:a", "192k",
                "-movflags", "+faststart",
                str(output_path)
            ]

        ok, err = await run_ffmpeg(args, timeout=600)
        if not ok:
            return False, err, {}

        # Probe output
        out_info = await get_video_info(output_path)
        if not out_info:
            return False, "output probe failed", {}

        info = {
            "path": str(output_path),
            "size_bytes": output_path.stat().st_size,
            "duration_s": out_info.get("duration_s"),
            "width": out_info.get("width"),
            "height": out_info.get("height"),
            "codec_video": out_info.get("codec_video"),
            "codec_audio": out_info.get("codec_audio"),
            "has_faststart": True,  # we set +faststart above
        }
        log.info("publisher_done", info=info)
        return True, "", info
