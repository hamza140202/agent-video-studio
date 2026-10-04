"""CLI entry point — `avs` command."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from avs import __version__

console = Console(stderr=True)
stdout_console = Console()


@click.group()
@click.version_option(__version__, prog_name="avs")
def cli() -> None:
    """agent-video-studio (avs) — multi-agent TikTok-to-YouTube compilation system."""


@cli.command()
@click.argument("brief")
@click.option("--urls-file", "-u", type=click.Path(exists=True), help="File with one URL per line")
@click.option("--reference", "-r", type=click.Path(exists=True), help="Reference video for style analysis (optional)")
@click.option("--output-dir", "-o", default="./output", help="Output directory")
@click.option("--json", "as_json", is_flag=True, help="Output JSON result to stdout")
def run(brief: str, urls_file: str | None, reference: str | None,
        output_dir: str, as_json: bool) -> None:
    """Produce a final compilation video from a content brief.

    BRIEF is the content idea, e.g. "Ahyeon September best of TikTok compilation".

    The Director orchestrates Researcher → StyleAnalyst → Editor → Critic → Publisher
    to produce a final YouTube-ready MP4.

    Examples:
      avs run "Best of Ahyeon TikTok September" --urls-file urls.txt
      avs run "Sad edits compilation" --urls-file urls.txt --reference ref.mp4
    """
    from avs.director import DefaultDirector

    director = DefaultDirector()
    result = asyncio.run(director.produce(
        brief,
        urls_file=Path(urls_file) if urls_file else None,
        reference_path=Path(reference) if reference else None,
        output_dir=Path(output_dir),
    ))

    if as_json:
        stdout_console.print_json(json.dumps(result.model_dump(mode="json"), default=str))
    else:
        if result.status == "ok":
            console.print(Panel.fit(
                f"[bold green]✅ Compilation produced[/]\n"
                f"[dim]Brief:[/] {brief}\n"
                f"[dim]Output:[/] {result.final_video_path}\n"
                f"[dim]Size:[/] {result.final_video_size_bytes:,} bytes "
                f"({result.final_video_size_bytes / 1048576:.1f} MB)\n"
                f"[dim]Duration:[/] {result.final_video_duration_s:.1f}s\n"
                f"[dim]Resolution:[/] {result.final_video_resolution}\n"
                f"[dim]Clips used:[/] {result.clips_used}",
                title="avs run — success", border_style="green",
            ))
        else:
            console.print(Panel.fit(
                f"[bold red]❌ Failed[/]\n[dim]Brief:[/] {brief}\n[red]Error:[/] {result.error}",
                title="avs run — failure", border_style="red",
            ))
    sys.exit(0 if result.status == "ok" else 1)


@cli.command()
def agents() -> None:
    """Show agent versions."""
    from avs.agents.researcher import AvdResearcher
    from avs.agents.style_analyst import TemplateStyleAnalyst
    from avs.agents.editor import FFmpegEditor
    from avs.agents.critic import FfprobeCritic
    from avs.agents.publisher import FFmpegPublisher
    from avs.director import DefaultDirector

    table = Table(title="Agent versions")
    table.add_column("Agent")
    table.add_column("Version")
    table.add_row("Director", DefaultDirector.__version__)
    table.add_row("Researcher", AvdResearcher.__version__)
    table.add_row("StyleAnalyst", TemplateStyleAnalyst.__version__)
    table.add_row("Editor", FFmpegEditor.__version__)
    table.add_row("Critic", FfprobeCritic.__version__)
    table.add_row("Publisher", FFmpegPublisher.__version__)
    console.print(table)


@cli.command(name="agent-instructions")
def agent_instructions_cmd() -> None:
    """Print step-by-step instructions for AI agents to use avs."""
    instructions = _AGENT_INSTRUCTIONS_TEMPLATE.replace("__VERSION__", __version__)
    from rich.markdown import Markdown
    console.print(Markdown(instructions))


_AGENT_INSTRUCTIONS_TEMPLATE = """# avs — agent usage instructions

**avs** (agent-video-studio) is a multi-agent system that turns a content brief + source TikTok URLs into a publish-ready YouTube compilation video. v__VERSION__.

## Step 1 — Install

```bash
pip install agent-video-studio
avs --version
```

The install also installs `agent-video-downloader` (avd) as a dependency. Run `avd agent-setup` first if you haven't already (installs ffmpeg + XHS-Downloader).

## Step 2 — Prepare source URLs

Create a `urls.txt` file with one TikTok URL per line:

```
https://www.tiktok.com/@<author>/video/<id1>
https://www.tiktok.com/@<author>/video/<id2>
...
```

Use `avd download '<url>'` to verify each URL works first.

## Step 3 — Produce the compilation

```bash
avs run "Best of Ahyeon September TikTok" --urls-file urls.txt --output-dir ./output
```

Optional: provide a reference video for style analysis:
```bash
avs run "Best of Ahyeon September TikTok" --urls-file urls.txt --reference ref.mp4
```

## Step 4 — Verify the output

```bash
ls -lh ./output/final.mp4
ffprobe ./output/final.mp4
```

The output is a 1920x1080 (16:9) MP4 with:
- Source vertical videos centered, scaled to fit frame height
- Blurred background (the same video scaled + gaussian blurred + darkened)
- Hard cuts between clips
- Optional title overlay at top
- Original audio preserved
- H.264 CRF 18 (high quality), AAC 192kbps, faststart for web

## Step 5 — JSON output for programmatic use

```bash
avs run "<brief>" --urls-file urls.txt --json
```

Returns the full `StudioResult` JSON on stdout.

## Architecture

Six agents (plain Python Protocol classes, no LLM in runtime loop):

- **Director** — orchestrates the pipeline, handles Critic retries
- **Researcher** — bulk-downloads source clips via `avd batch`
- **StyleAnalyst** — analyzes reference video OR uses documented template
- **Editor** — composes final video (ffmpeg filter_complex: blurred bg + centered fg)
- **Critic** — verifies output (resolution, duration, audio, file size)
- **Publisher** — final high-quality H.264 encode with faststart

## Agent decision tree (copy this into your agent prompt)

    IF user asks for a TikTok compilation video →
      1. ask user for topic + URLs file
      2. run: avs run "<topic>" --urls-file urls.txt
      3. IF exit 0 → final.mp4 is at ./output/final.mp4
      4. IF exit 1 → read stderr for error, retry with adjustments

## Config

None — avs is config-free. The style is determined by the StyleAnalyst
(reference if provided, documented template otherwise).

## Documentation

- README.md — install + quickstart
- CLAUDE.md — project memory (read first if modifying)
- https://github.com/hamza140202/agent-video-studio

## License

MIT.
"""



def main() -> None:
    cli()


if __name__ == "__main__":
    main()
