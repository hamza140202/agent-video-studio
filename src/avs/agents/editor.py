"""Editor agent — composes the final compilation video from source clips.

The Editor uses ffmpeg to:
  1. Trim each source clip to max_clip_duration_s
  2. Scale each clip to fit the output frame's height (preserve aspect)
  3. Composite: blurred background (scaled+blurred source) + centered source
  4. Concatenate all processed clips with hard cuts or crossfades
  5. (Optional) Add title overlay at top
  6. Output: single MP4 at the target resolution

For a 16:9 (1920x1080) frame with 9:16 (vertical) source:
  - Background: source scaled to 1920x1080 (covers full frame), blurred sigma=20,
    brightness=0.5
  - Foreground (centered): source scaled to height=1080, width auto (e.g. 608x1080),
    centered horizontally
  - Result: 1920x1080 with vertical video in center, blurred video as bg
"""
from __future__ import annotations

import asyncio
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Protocol

from avs.models import Clip, StyleProfile
from avs.utils.ff import run_ffmpeg
from avs.utils.logging import get_logger

log = get_logger("avs.editor")


class Editor(Protocol):
    async def edit(self, clips: list[Clip], style: StyleProfile,
                   *, output_path: Path) -> tuple[bool, str]:
        """Returns (ok, error_message)."""
        ...


class FFmpegEditor:
    """Editor that uses ffmpeg directly (no moviepy dependency).

    The pipeline is:
      For each clip:
        1. Trim to max_clip_duration_s (if longer)
        2. Build filter_complex: blurred bg + centered fg
        3. Encode to intermediate MP4 at target resolution
      Then:
        4. Concatenate all intermediates into the final output MP4
    """

    __version__ = "1.0.0"

    async def edit(self, clips: list[Clip], style: StyleProfile,
                   *, output_path: Path) -> tuple[bool, str]:
        if not clips:
            return False, "no clips to edit"

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Stage 1: process each clip into an intermediate at target resolution
        intermediates: list[Path] = []
        tmp_dir = Path(tempfile.mkdtemp(prefix="avs-edit-"))

        try:
            for i, clip in enumerate(clips):
                if not clip.local_path or not Path(clip.local_path).exists():
                    log.warning("editor_skip_missing_clip", clip_index=i)
                    continue
                intermediate = tmp_dir / f"clip_{i:03d}.mp4"
                ok, err = await self._process_clip(clip, style, intermediate)
                if not ok:
                    log.warning("editor_clip_failed", clip_index=i, error=err)
                    continue
                intermediates.append(intermediate)
                log.info("editor_clip_done", clip_index=i, path=str(intermediate))

            if not intermediates:
                return False, "all clips failed to process"

            # Stage 2: concatenate intermediates
            log.info("editor_concat_start", count=len(intermediates))
            ok, err = await self._concat(intermediates, output_path, style)
            if not ok:
                return False, f"concat failed: {err}"

            log.info("editor_done", output=str(output_path), clip_count=len(intermediates))
            return True, ""
        finally:
            # Clean up intermediates
            try:
                shutil.rmtree(tmp_dir)
            except OSError:
                pass

    async def _process_clip(self, clip: Clip, style: StyleProfile,
                             output: Path) -> tuple[bool, str]:
        """Process a single clip: trim, scale, composite bg+fg."""
        src = str(clip.local_path)
        W, H = style.output_width, style.output_height
        max_dur = style.max_clip_duration_s

        # Determine source dimensions for filter
        # If source is vertical (e.g. 1080x1920), scale fg to height=H, width auto
        # If source is horizontal, scale fg to fit within W x H preserving aspect
        if clip.height and clip.width:
            if clip.height > clip.width:
                # Vertical source: scale to height=H
                fg_w = -2  # auto
                fg_h = H
            else:
                # Horizontal source: scale to width=W, height auto (but cap at H)
                fg_w = W
                fg_h = -2
        else:
            # Unknown dimensions: assume vertical
            fg_w = -2
            fg_h = H

        # Build the filter_complex
        # 1. Split input into two streams: bg and fg
        # 2. bg: scale to WxH (covers full frame, may crop), blur, brightness
        # 3. fg: scale to fit, overlay at center
        # 4. (optional) drawtext for title

        # For blurred background:
        #   [0:v]split[bg][fg];
        #   [bg]scale=W:H:force_original_aspect_ratio=increase,crop=W:H,boxblur=sigma:brightness=0.5[bgout];
        #   [fg]scale=fg_w:fg_h[fgout];
        #   [bgout][fgout]overlay=(W-fg_w)/2:0[outv]
        # Note: filter_complex overlay coordinates must be exact

        if style.background_mode == "blurred_source":
            # Compute overlay position
            if style.clip_position_x == "center":
                overlay_x = "(W-w)/2"
            elif style.clip_position_x == "left":
                overlay_x = "0"
            else:
                overlay_x = "(W-w)/2"
            if style.clip_position_y == "center":
                overlay_y = "(H-h)/2"
            else:
                overlay_y = "0"

            # Build filter (with proper stream labels for each step)
            filter_complex = (
                f"[0:v]split=2[bg][fg];"
                f"[bg]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
                f"boxblur=luma_radius={style.background_blur_sigma}:luma_power=1,"
                f"eq=brightness={style.background_brightness - 1.0}[bgout];"
                f"[fg]scale={fg_w}:{fg_h}[fgout];"
                f"[bgout][fgout]overlay={overlay_x}:{overlay_y},format=yuv420p[composed]"
            )
        elif style.background_mode == "solid_color":
            color = style.background_solid_color.replace("0x", "0x")
            filter_complex = (
                f"color=c={color}:s={W}x{H}[bg];"
                f"[0:v]scale={fg_w}:{fg_h}[fg];"
                f"[bg][fg]overlay=(W-w)/2:(H-h)/2,format=yuv420p[composed]"
            )
        else:
            filter_complex = f"[0:v]scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(W-w)/2:(H-h)/2,format=yuv420p[composed]"

        # Add title overlay if requested (chained after [composed], output as [outv])
        if style.title_overlay and style.title_position != "none":
            safe_title = str(style.title_overlay).replace(":", r"\:").replace("'", r"'\''")
            font_size = style.title_font_size
            filter_complex += (
                f";[composed]drawbox=x=0:y=0:w=iw:h={font_size + 30}:color=black@0.5:t=fill[composed2];"
                f"[composed2]drawtext=fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf:"
                f"text='{safe_title}':fontcolor={style.title_color}:"
                f"fontsize={font_size}:x=(w-text_w)/2:y=20[outv]"
            )
        else:
            # Just rename [composed] to [outv]
            filter_complex += "[outv]"

        # Build ffmpeg args
        args = []
        # Input seek for trimming (efficiency)
        if clip.duration_s and clip.duration_s > max_dur:
            args.extend(["-t", f"{max_dur}"])
        args.extend(["-i", src])

        # Filter
        args.extend(["-filter_complex", filter_complex, "-map", "[outv]"])

        # Audio: copy from source (or no audio if source has none)
        args.extend(["-map", "0:a?", "-c:a", "aac", "-b:a", "192k"])

        # Video encode
        args.extend([
            "-c:v", "libx264", "-preset", "medium", "-crf", str(style.output_crf),
            "-pix_fmt", "yuv420p",
            "-r", str(style.output_fps),
            "-s", f"{W}x{H}",
        ])

        # Output
        args.extend(["-movflags", "+faststart", str(output)])

        return await run_ffmpeg(args, timeout=120)

    async def _concat(self, clips: list[Path], output: Path,
                       style: StyleProfile) -> tuple[bool, str]:
        """Concatenate processed clips using the concat demuxer."""
        # Build concat list file
        list_file = output.parent / "_concat_list.txt"
        with open(list_file, "w") as f:
            for clip in clips:
                # Need to escape path for concat demuxer: ' -> backslash-backslash-quote
                safe_path = str(clip).replace("'", r"\'")
                f.write(f"file '{safe_path}'\n")

        args = [
            "-f", "concat", "-safe", "0", "-i", str(list_file),
            "-c", "copy", "-movflags", "+faststart", str(output)
        ]
        ok, err = await run_ffmpeg(args, timeout=180)
        list_file.unlink(missing_ok=True)
        return ok, err
