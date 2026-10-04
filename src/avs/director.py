"""Director agent — orchestrates the full studio pipeline.

Receives a content brief (e.g. "Ahyeon September best of TikTok compilation")
and coordinates the Researcher → StyleAnalyst → Editor → Critic → Publisher
pipeline.

If the Critic returns verdict="retry", the Director applies the suggestions
and re-runs the Editor + Critic (max 1 retry).
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Protocol

from avs.models import StudioResult, StyleProfile, ResearchReport
from avs.utils.logging import get_logger

log = get_logger("avs.director")


class Director(Protocol):
    async def produce(self, brief: str, *, urls_file: Path | None = None,
                      urls: list[str] | None = None,
                      reference_path: Path | None = None,
                      output_dir: Path = Path("./output")) -> StudioResult:
        ...


class DefaultDirector:
    """Director that wires AvdResearcher → TemplateStyleAnalyst → FFmpegEditor
    → FfprobeCritic → FFmpegPublisher.
    """

    __version__ = "1.0.0"

    def __init__(self) -> None:
        from avs.agents.researcher import AvdResearcher
        from avs.agents.style_analyst import TemplateStyleAnalyst
        from avs.agents.editor import FFmpegEditor
        from avs.agents.critic import FfprobeCritic
        from avs.agents.publisher import FFmpegPublisher

        self.researcher = AvdResearcher()
        self.style_analyst = TemplateStyleAnalyst()
        self.editor = FFmpegEditor()
        self.critic = FfprobeCritic()
        self.publisher = FFmpegPublisher()

    async def produce(self, brief: str, *, urls_file: Path | None = None,
                      urls: list[str] | None = None,
                      reference_path: Path | None = None,
                      output_dir: Path = Path("./output")) -> StudioResult:
        started = datetime.utcnow()
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        log.info("director_start", brief=brief)

        # Stage 1: Research (download source clips)
        log.info("director_research_start")
        research: ResearchReport = await self.researcher.research(
            brief, urls_file=urls_file, urls=urls,
            dest=output_dir / "raw"
        )
        if not research.clips:
            return StudioResult(
                brief=brief, status="failed",
                error="research produced no clips",
                research_report=research, started_at=started,
                finished_at=datetime.utcnow(),
            )
        log.info("director_research_done", clip_count=len(research.clips))

        # Stage 2: Style analysis
        log.info("director_style_start")
        style: StyleProfile = await self.style_analyst.analyze(
            reference_path, brief=brief
        )
        log.info("director_style_done", output_resolution=f"{style.output_width}x{style.output_height}")

        # Stage 3: Edit (compose final video)
        edited_path = output_dir / "edited.mp4"
        log.info("director_edit_start", path=str(edited_path))
        ok, err = await self.editor.edit(research.clips, style, output_path=edited_path)
        if not ok:
            return StudioResult(
                brief=brief, status="failed", error=f"edit failed: {err}",
                research_report=research, style_profile=style,
                started_at=started, finished_at=datetime.utcnow(),
            )
        log.info("director_edit_done", path=str(edited_path))

        # Stage 4: Critique
        log.info("director_critique_start")
        critique = await self.critic.critique(
            edited_path, research.clips, style, round=0
        )
        log.info("director_critique_done", verdict=critique.verdict, score=critique.score)

        # If retry, re-edit with suggestions
        if critique.verdict == "retry":
            log.info("director_retry", suggestions=critique.suggestions)
            # Apply suggestion: if duration mismatch, lower max_clip_duration
            for s in critique.suggestions:
                if "max_clip_duration" in s.lower():
                    style.max_clip_duration_s = max(10, style.max_clip_duration_s * 0.75)
            ok, err = await self.editor.edit(research.clips, style, output_path=edited_path)
            if ok:
                critique = await self.critic.critique(
                    edited_path, research.clips, style, round=1
                )
                log.info("director_critique_done_retry", verdict=critique.verdict)

        # Stage 5: Publish
        final_path = output_dir / "final.mp4"
        log.info("director_publish_start", path=str(final_path))
        pub_ok, pub_err, pub_info = await self.publisher.publish(
            edited_path, final_path, style
        )
        if not pub_ok:
            return StudioResult(
                brief=brief, status="failed", error=f"publish failed: {pub_err}",
                research_report=research, style_profile=style, critique_report=critique,
                started_at=started, finished_at=datetime.utcnow(),
            )

        result = StudioResult(
            brief=brief, status="ok",
            final_video_path=final_path,
            final_video_size_bytes=pub_info.get("size_bytes", 0),
            final_video_duration_s=pub_info.get("duration_s", 0) or 0,
            final_video_resolution=f"{pub_info.get('width','?')}x{pub_info.get('height','?')}",
            clips_used=len(research.clips),
            research_report=research,
            style_profile=style,
            critique_report=critique,
            started_at=started,
            finished_at=datetime.utcnow(),
        )
        log.info("director_done", brief=brief, status=result.status,
                 final_path=str(result.final_video_path),
                 size=result.final_video_size_bytes,
                 duration=result.final_video_duration_s,
                 resolution=result.final_video_resolution)
        return result
