"""Auto-discovery Researcher — finds TikTok URLs by topic, no urls.txt needed.

Multi-backend search architecture:
  1. z-ai web_search SDK (primary)
  2. DuckDuckGo HTML search (fallback)
  3. Direct TikTok user profile scraping (last resort, via curl_cffi)
  4. Direct TikTok search page scraping (last resort, via curl_cffi)

Each backend is tried in order. If one returns 0 URLs, fall back to the next.
URLs found by any backend are merged, deduplicated, and verified via TikWM's
single-post endpoint (`/api/?url=<url>&hd=1`).

The agent filters by topic keywords in the verified title and returns the
top N URLs by play_count.
"""
from __future__ import annotations

import asyncio
import json
import re
import subprocess
from pathlib import Path
from typing import Protocol

from avs.models import Clip, ResearchReport
from avs.utils.ff import get_video_info
from avs.utils.logging import get_logger

log = get_logger("avs.auto_discovery")

TIKTOK_URL_REGEX = r'https?://www\.tiktok\.com/@[\w.\-]+/video/\d+'
TIKWM_API = 'https://www.tikwm.com/api/'


class AutoDiscoveryResearcher:
    """Researcher that auto-discovers TikTok URLs by topic.

    No urls.txt required — the agent searches the web for TikTok URLs
    matching the topic, verifies each via TikWM, and returns the top N.

    The agent uses multiple search backends in priority order:
      1. z-ai web_search SDK (returns JSON results)
      2. DuckDuckGo HTML search (regex extraction from result HTML)
      3. TikTok user profile scraping (curl_cffi with chrome131 impersonation)

    Each backend is tried; if it returns 0 candidate URLs, the next is tried.
    URLs from all backends are merged, deduplicated, verified, and ranked.
    """

    __version__ = "1.0.0"

    async def research(self, brief: str, *, urls_file: Path | None = None,
                       urls: list[str] | None = None,
                       dest: Path = Path("./downloads/raw"),
                       max_results: int = 25) -> ResearchReport:
        """Auto-discover TikTok URLs by topic, then bulk-download via avd.

        Args:
            brief: Content brief / topic (e.g. "Ahyeon September best of TikTok")
            urls_file: Optional explicit URLs file (skips discovery)
            urls: Optional explicit URL list (skips discovery)
            dest: Destination directory for downloaded clips
            max_results: Max number of URLs to discover (default 25)
        """
        dest = Path(dest)
        dest.mkdir(parents=True, exist_ok=True)

        # If explicit URLs provided, skip discovery
        if urls or urls_file:
            return await self._download_explicit(brief, urls_file, urls, dest)

        # Otherwise: auto-discover
        log.info("auto_discovery_start", brief=brief, max_results=max_results)
        candidate_urls = await self._discover_urls(brief, max_results=max_results)
        if not candidate_urls:
            return ResearchReport(
                brief=brief, source="auto_discovery", clips=[],
                notes="No URLs discovered — all search backends returned 0 candidates. "
                      "Provide a urls.txt file explicitly or wait for search rate limits to clear.",
            )

        # Verify each URL via TikWM
        verified = await self._verify_urls(candidate_urls, brief)
        log.info("auto_discovery_verified", count=len(verified), brief=brief)

        if not verified:
            return ResearchReport(
                brief=brief, source="auto_discovery", clips=[],
                notes=f"Found {len(candidate_urls)} candidate URLs but none verified via TikWM",
            )

        # Take top N by play_count
        verified.sort(key=lambda x: x.get("play_count", 0), reverse=True)
        top_urls = [v["url"] for v in verified[:max_results]]
        log.info("auto_discovery_top", count=len(top_urls), brief=brief)

        # Bulk download via avd
        return await self._download_urls(brief, top_urls, dest)

    async def _discover_urls(self, topic: str, *, max_results: int = 25) -> list[str]:
        """Try multiple search backends to find candidate TikTok URLs."""
        candidates: set[str] = set()

        # Backend 1: z-ai web search
        try:
            urls = await self._search_via_z_ai(topic)
            log.info("discovery_z_ai", found=len(urls))
            candidates.update(urls)
        except Exception as e:
            log.warning("discovery_z_ai_failed", error=str(e))

        # Backend 2: DuckDuckGo HTML
        if len(candidates) < max_results:
            try:
                urls = await self._search_via_ddg(topic)
                log.info("discovery_ddg", found=len(urls))
                candidates.update(urls)
            except Exception as e:
                log.warning("discovery_ddg_failed", error=str(e))

        # Backend 3: TikTok user profile scraping
        if len(candidates) < max_results:
            try:
                urls = await self._search_via_tiktok_profiles(topic)
                log.info("discovery_tiktok_profiles", found=len(urls))
                candidates.update(urls)
            except Exception as e:
                log.warning("discovery_tiktok_profiles_failed", error=str(e))

        return sorted(candidates)

    async def _search_via_z_ai(self, topic: str) -> list[str]:
        """Use z-ai web_search CLI to find TikTok URLs."""
        # Build search query
        query = f"{topic} site:tiktok.com"
        # Call z-ai CLI
        try:
            proc = await asyncio.create_subprocess_exec(
                "z-ai", "function", "-n", "web_search",
                "-a", json.dumps({"query": query, "num": 30}),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
            output = stdout.decode(errors="replace") if stdout else ""
        except Exception as e:
            log.warning("z_ai_call_failed", error=str(e))
            return []

        # Extract TikTok URLs from the JSON output
        return re.findall(TIKTOK_URL_REGEX, output)

    async def _search_via_ddg(self, topic: str) -> list[str]:
        """Use DuckDuckGo HTML search."""
        import urllib.parse
        query = urllib.parse.quote(f"{topic} site:tiktok.com")
        url = f"https://html.duckduckgo.com/html/?q={query}"

        try:
            from curl_cffi import requests as cc
            r = await asyncio.to_thread(
                cc.get, url, impersonate="chrome131", timeout=20,
                headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"}
            )
            if r.status_code != 200:
                return []
            return list(set(re.findall(TIKTOK_URL_REGEX, r.text)))
        except Exception as e:
            log.warning("ddg_call_failed", error=str(e))
            return []

    async def _search_via_tiktok_profiles(self, topic: str) -> list[str]:
        """Scrape TikTok user profile pages for video URLs.

        Extracts candidate usernames from the topic (e.g. "ahyeon" → try @ahyeon.babymonster, @ahyeonclips, etc.)
        Then fetches each profile page and extracts video URLs from the embedded JSON.
        """
        # Extract candidate usernames from the topic
        topic_lower = topic.lower()
        candidates = set()

        # Common patterns for K-pop fan accounts
        keywords = re.findall(r'[a-z]+', topic_lower)
        for kw in keywords[:3]:  # top 3 keywords
            for suffix in ['', '.babymonster', '_edit', 'clips', '_babymonster', '.edits', 'world', 'center', '_edits']:
                candidates.add(f"{kw}{suffix}")
        # Add known Babymonster-related accounts if topic mentions babymonster/ahyeon/etc
        if any(k in topic_lower for k in ['babymonster', 'ahyeon', 'rama', 'pharita', 'asa', 'ruka', 'chiquita', 'rora']):
            candidates.update([
                'babymonster_yg_tiktok', 'danhee.wave.kr', 'tzuwice.ytb',
            ])

        all_urls: set[str] = set()
        for user in list(candidates)[:10]:  # limit to 10 user probes
            try:
                from curl_cffi import requests as cc
                r = await asyncio.to_thread(
                    cc.get, f"https://www.tiktok.com/@{user}", impersonate="chrome131", timeout=20,
                    headers={
                        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                        "Accept-Language": "en-US,en;q=0.9",
                    }
                )
                if r.status_code != 200:
                    continue
                # Try to extract from universal data blob
                m = re.search(r'<script[^>]*id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(\{.+?\})</script>', r.text, re.DOTALL)
                if m:
                    try:
                        data = json.loads(m.group(1))
                        scopes = data.get("__DEFAULT_SCOPE__', {}")
                        user_page = scopes.get("webapp.user-detail", {})
                        # Look for video items
                        for key in ["ItemList", "items", "posts"]:
                            items = user_page.get(key)
                            if isinstance(items, dict):
                                items = items.get("posts", [])
                            if isinstance(items, list):
                                for item in items:
                                    vid_id = item.get("id") or (item.get("video", {}) or {}).get("id")
                                    if vid_id:
                                        all_urls.add(f"https://www.tiktok.com/@{user}/video/{vid_id}")
                                break
                    except Exception:
                        pass
                # Also try direct regex
                urls_found = re.findall(TIKTOK_URL_REGEX, r.text)
                all_urls.update(urls_found)
            except Exception:
                continue

        return list(all_urls)

    async def _verify_urls(self, urls: list[str], topic: str) -> list[dict]:
        """Verify each URL via TikWM's single-post endpoint. Returns list of dicts."""
        verified: list[dict] = []
        # Be polite — 0.5s between requests
        import urllib.parse

        for url in urls:
            try:
                from curl_cffi import requests as cc
                api = f"{TIKWM_API}?url={urllib.parse.quote(url, safe='')}&hd=1"
                r = await asyncio.to_thread(
                    cc.get, api, impersonate="chrome131", timeout=20,
                    headers={"User-Agent": "Mozilla/5.0"}
                )
                if r.status_code != 200:
                    continue
                data = r.json()
                if data.get("code") != 0:
                    continue
                d = data.get("data") or {}
                # Reconstruct URL from data
                video_id = d.get("id")
                author = (d.get("author") or {}).get("unique_id") or ""
                play_url = d.get("play") or d.get("hdplay")
                duration = d.get("duration", 0)
                play_count = d.get("stats", {}).get("playCount", 0) if isinstance(d.get("stats"), dict) else d.get("play_count", 0)
                title = d.get("title", "")

                if not play_url:
                    continue

                verified.append({
                    "url": url,
                    "video_id": video_id,
                    "author": author,
                    "title": title,
                    "duration_s": duration,
                    "play_count": play_count,
                })
                await asyncio.sleep(0.5)  # be polite
            except Exception as e:
                log.warning("verify_url_failed", url=url, error=str(e))
                continue

        # Filter by topic keywords in title
        topic_keywords = [k.lower() for k in re.findall(r'[a-z]+', topic.lower()) if len(k) > 2]
        if topic_keywords:
            filtered = []
            for v in verified:
                title_lower = v["title"].lower()
                if any(kw in title_lower for kw in topic_keywords):
                    filtered.append(v)
            # If filter is too strict (no matches), keep all verified
            if filtered:
                verified = filtered

        return verified

    async def _download_urls(self, brief: str, urls: list[str], dest: Path) -> ResearchReport:
        """Download verified URLs via avd batch."""
        import shutil
        avd_bin = shutil.which("avd") or "avd"

        batch_file = dest / "_batch_input.txt"
        with open(batch_file, "w") as f:
            for url in urls:
                f.write(url + "\n")

        log.info("auto_discovery_download_start", url_count=len(urls), dest=str(dest))
        proc = await asyncio.create_subprocess_exec(
            avd_bin, "batch", str(batch_file),
            "--dest", str(dest), "--concurrency", "2",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            await asyncio.wait_for(proc.communicate(), timeout=600)
        except asyncio.TimeoutExpired:
            proc.kill()
            await proc.communicate()
            log.warning("auto_discovery_download_timeout")

        # Collect downloaded MP4 files
        clips: list[Clip] = []
        for mp4_path in sorted(dest.glob("*.mp4")):
            clip = Clip(
                url=_match_url_to_path(mp4_path, urls),
                local_path=mp4_path,
            )
            info = await get_video_info(mp4_path)
            if info:
                clip.width = info.get("width")
                clip.height = info.get("height")
                clip.duration_s = info.get("duration_s")
                clip.aspect = "vertical" if info.get("aspect_orientation") == "vertical" else "unknown"
            clips.append(clip)

        log.info("auto_discovery_download_done", clips_downloaded=len(clips))
        batch_file.unlink(missing_ok=True)

        return ResearchReport(
            brief=brief, source="auto_discovery", clips=clips,
            notes=f"Auto-discovered and downloaded {len(clips)} of {len(urls)} URLs",
        )

    async def _download_explicit(self, brief: str, urls_file: Path | None,
                                  urls: list[str] | None, dest: Path) -> ResearchReport:
        """Fall back to explicit URL list (like AvdResearcher)."""
        # Reuse AvdResearcher logic
        from avs.agents.researcher import AvdResearcher
        return await AvdResearcher().research(
            brief, urls_file=urls_file, urls=urls, dest=dest
        )


def _match_url_to_path(path: Path, urls: list[str]) -> str:
    name = path.stem
    for url in urls:
        if name in url:
            return url
    return f"<unknown> (file: {path.name})"
