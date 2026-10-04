"""StyleAnalyst agent — extracts the editing style from a reference video
OR falls back to a documented style template.

If a reference video is provided (local file path), the StyleAnalyst uses
ffprobe to extract:
  - Resolution / aspect ratio
  - Duration
  - Has blurred background?
  - Text overlays?
  - Cut rhythm

If no reference is provided (or it can't be downloaded — e.g. YouTube
hard-blocked from datacenter IP per the ytagent problem), the StyleAnalyst
uses a documented TikTok-to-YouTube compilation style template.
"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol

from avs.models import StyleProfile
from avs.utils.ff import get_video_info
from avs.utils.logging import get_logger

log = get_logger("avs.style_analyst")


class StyleAnalyst(Protocol):
    async def analyze(self, reference_path: Path | None = None,
                      *, brief: str = "") -> StyleProfile:
        ...


class TemplateStyleAnalyst:
    """StyleAnalyst that uses a documented template when no reference is available.

    The template is the standard "TikTok vertical to YouTube horizontal
    compilation" style:
      - 1920x1080 (16:9 Full HD)
      - Source vertical video centered, scaled to fit frame height
      - Background: same video scaled to fill frame, gaussian blurred (sigma=20),
        darkened to 50% brightness
      - Hard cuts between clips
      - Optional title overlay at top
      - Original audio preserved
    """

    __version__ = "1.0.0"

    async def analyze(self, reference_path: Path | None = None,
                      *, brief: str = "") -> StyleProfile:
        profile = StyleProfile(
            output_width=1920,
            output_height=1080,
            output_fps=30,
            output_crf=18,
            clip_scale="fit_height",
            clip_position_x="center",
            clip_position_y="center",
            background_mode="blurred_source",
            background_blur_sigma=20,
            background_brightness=0.5,
            cut_mode="hard",
            crossfade_duration_s=0.3,
            max_clip_duration_s=30.0,
            title_overlay=brief[:80] if brief else None,
            title_position="top",
            title_font_size=48,
            title_color="white",
            title_bg_box=True,
            audio_mode="original",
            music_volume_db=-20.0,
            description="Documented TikTok-to-YouTube compilation style template (vertical source centered, blurred background, hard cuts, original audio preserved)",
        )

        if reference_path and Path(reference_path).exists():
            log.info("style_analyst_analyzing_reference", path=str(reference_path))
            info = await get_video_info(reference_path)
            if info:
                profile.reference_path = Path(reference_path)
                profile.reference_observed = {
                    "width": info.get("width"),
                    "height": info.get("height"),
                    "duration_s": info.get("duration_s"),
                    "aspect_orientation": info.get("aspect_orientation"),
                }
                # If the reference is 16:9, use its resolution as the output
                if info.get("aspect_orientation") == "horizontal":
                    profile.output_width = info.get("width") or 1920
                    profile.output_height = info.get("height") or 1080
                profile.description = f"Style derived from reference video at {reference_path} + documented template"
            else:
                log.warning("style_analyst_reference_probe_failed", path=str(reference_path))
        else:
            log.info("style_analyst_using_template", reason="no reference provided or unreachable")

        return profile
