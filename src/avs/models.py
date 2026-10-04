"""Pydantic data models for inter-agent payloads."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

# --- Type aliases ---
ClipType = Literal["vertical", "horizontal", "unknown"]
Stage = Literal["research", "style", "edit", "critique", "publish"]
Status = Literal["ok", "empty", "failed"]


class Clip(BaseModel):
    """A source clip to be composed into the final compilation."""

    url: str
    local_path: Path | None = None
    duration_s: float | None = None
    width: int | None = None
    height: int | None = None
    aspect: ClipType = "unknown"
    title: str | None = None
    author: str | None = None
    source_platform_post_id: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class StyleProfile(BaseModel):
    """The editing style to apply to the compilation."""

    # Output format
    output_width: int = 1920
    output_height: int = 1080
    output_fps: int = 30
    output_crf: int = 18  # H.264 quality (lower = higher quality)

    # Source clip placement
    clip_scale: str = "fit_height"  # fit_height | fill | stretch
    clip_position_x: str = "center"  # center | left | right
    clip_position_y: str = "center"

    # Background
    background_mode: str = "blurred_source"  # blurred_source | solid_color | none
    background_blur_sigma: int = 20
    background_brightness: float = 0.5  # 0-1, 1 = full brightness
    background_solid_color: str = "0x000000"

    # Cuts & transitions
    cut_mode: str = "hard"  # hard | crossfade
    crossfade_duration_s: float = 0.3
    max_clip_duration_s: float = 30.0  # cap long clips at 30s

    # Text overlay (optional)
    title_overlay: str | None = None
    title_position: str = "top"  # top | bottom | none
    title_font_size: int = 48
    title_color: str = "white"
    title_bg_box: bool = True

    # Audio
    audio_mode: str = "original"  # original | music_track | mixed
    music_track_path: Path | None = None
    music_volume_db: float = -20.0  # background music volume

    # Reference analysis (filled by StyleAnalyst)
    reference_path: Path | None = None
    reference_observed: dict[str, Any] = Field(default_factory=dict)

    # Metadata
    created_at: datetime = Field(default_factory=datetime.utcnow)
    description: str = "Documented TikTok-to-YouTube compilation style template"


class ResearchReport(BaseModel):
    """Output of the Researcher agent."""

    brief: str
    clips: list[Clip] = Field(default_factory=list)
    source: str = "avd_batch"  # avd_batch | urls_file | search
    notes: str = ""


class CritiqueReport(BaseModel):
    """Output of the Critic agent."""

    verdict: Literal["pass", "retry", "fail"]
    issues: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
    score: float = 0.0  # 0-1
    critique_round: int = 0


class StudioResult(BaseModel):
    """Final result of the full studio pipeline."""

    brief: str
    status: Status
    final_video_path: Path | None = None
    final_video_size_bytes: int = 0
    final_video_duration_s: float = 0.0
    final_video_resolution: str = ""
    clips_used: int = 0
    research_report: ResearchReport | None = None
    style_profile: StyleProfile | None = None
    critique_report: CritiqueReport | None = None
    error: str | None = None
    started_at: datetime = Field(default_factory=datetime.utcnow)
    finished_at: datetime | None = None
