"""Researcher agent — finds source clips for a given content brief.

Inputs: content brief (e.g. "Ahyeon September best of TikTok compilation")
Outputs: ResearchReport with list of local Clip paths

The Researcher uses the `avd` tool (agent-video-downloader) to bulk-download
verified URLs. It can:
  - Read a URLs file (one URL per line)
  - Accept an explicit list of URLs
  - (Future) Use web search + TikTok user posts endpoint to discover URLs
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Protocol

from avs.models import Clip, ResearchReport
from avs.utils.ff import get_video_info
from avs.utils.logging import get_logger

log = get_logger("avs.researcher")


class Researcher(Protocol):
    async def research(self, brief: str, *, urls_file: Path | None = None,
                       urls: list[str] | None = None,
                       dest: Path = Path("./downloads/raw")) -> ResearchReport:
        ...


class AvdResearcher:
    """Researcher that uses the `avd` CLI to download source clips."""

    __version__ = "1.0.0"

    def __init__(self) -> None:
        # Find the avd binary
        import shutil
        self.avd_bin = shutil.which("avd") or "avd"

    async def research(self, brief: str, *, urls_file: Path | None = None,
                       urls: list[str] | None = None,
                       dest: Path = Path("./downloads/raw")) -> ResearchReport:
        """Download source clips for the brief.

        Args:
            brief: Content brief (e.g. "Ahyeon September best of TikTok compilation")
            urls_file: Optional file with one URL per line
            urls: Optional explicit URL list (overrides urls_file)
            dest: Destination directory for downloaded clips
        """
        dest = Path(dest)
        dest.mkdir(parents=True, exist_ok=True)

        # Resolve URL list
        url_list: list[str] = []
        if urls:
            url_list = list(urls)
        elif urls_file:
            with open(urls_file) as f:
                url_list = [line.strip() for line in f if line.strip() and not line.startswith("#")]
        else:
            return ResearchReport(
                brief=brief, source="avd_batch", clips=[],
                notes="No URLs provided — pass urls_file or urls argument"
            )

        if not url_list:
            return ResearchReport(brief=brief, source="avd_batch", clips=[], notes="No URLs in input")

        # Run `avd batch <file> --dest <dest> --concurrency 2`
        log.info("researcher_start", brief=brief, url_count=len(url_list), dest=str(dest))

        # Write URLs to a temp file for avd batch
        batch_file = dest / "_batch_input.txt"
        with open(batch_file, "w") as f:
            for url in url_list:
                f.write(url + "\n")

        # Invoke avd batch
        proc = await asyncio.create_subprocess_exec(
            self.avd_bin, "batch", str(batch_file),
            "--dest", str(dest), "--concurrency", "2",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=600)
        except asyncio.TimeoutExpired:
            proc.kill()
            await proc.communicate()
            log.warning("researcher_timeout", brief=brief)
            # Continue with whatever was downloaded

        # Collect downloaded MP4 files
        clips: list[Clip] = []
        for mp4_path in sorted(dest.glob("*.mp4")):
            clip = Clip(
                url=_match_url_to_path(mp4_path, url_list),
                local_path=mp4_path,
            )
            # Probe for width/height/duration
            info = await get_video_info(mp4_path)
            if info:
                clip.width = info.get("width")
                clip.height = info.get("height")
                clip.duration_s = info.get("duration_s")
                clip.aspect = "vertical" if info.get("aspect_orientation") == "vertical" else (
                    "horizontal" if info.get("aspect_orientation") == "horizontal" else "unknown"
                )
            clips.append(clip)

        log.info("researcher_done", brief=brief, clips_downloaded=len(clips))
        batch_file.unlink(missing_ok=True)

        return ResearchReport(
            brief=brief, source="avd_batch", clips=clips,
            notes=f"Downloaded {len(clips)} of {len(url_list)} URLs via avd"
        )


def _match_url_to_path(path: Path, urls: list[str]) -> str:
    """Try to match a downloaded file back to its source URL."""
    name = path.stem
    for url in urls:
        if name in url or any(part in url for part in [name, name[-15:]]):
            return url
    return f"<unknown> (file: {path.name})"
