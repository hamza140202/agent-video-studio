"""Telegram Bot API client — sends final video to a Telegram chat.

For files <= 50 MB: uses sendVideo (inline playback in Telegram).
For files > 50 MB: uses storage.to (https://storage.to) to host the full-quality
  video (up to 25 GB), then sends a text message with the shareable download link
  via sendMessage. This bypasses Telegram's 50 MB Bot API upload limit entirely
  and delivers the FULL video at original quality.

storage.to is a no-signup file hosting service with a documented 3-step upload
API (init → PUT → confirm). Anonymous uploads: 50 files / 24h, 100 GB bandwidth,
files expire in 3 days. See https://storage.to/docs/api.

Env vars:
  AVS_TG_BOT_TOKEN   — Telegram bot token (required)
  AVS_TG_CHAT_ID     — Telegram chat ID (required)
  AVS_STORAGE_TO_VISITOR_TOKEN — Optional storage.to visitor token (auto-generated)
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx

TELEGRAM_API = "https://api.telegram.org"
TELEGRAM_BOT_API_LIMIT = 50 * 1024 * 1024  # 50 MB hard limit on standard Bot API uploads


async def send_video_to_telegram(video_path: Path, caption: str = "",
                                  bot_token: str | None = None,
                                  chat_id: str | None = None) -> tuple[bool, str]:
    """Send a video file to a Telegram chat.

    Strategy:
      - File <= 50 MB: sendVideo (inline playback in Telegram)
      - File > 50 MB: upload to storage.to (full quality, no re-encoding),
        then sendMessage with the download link

    Args:
        video_path: Path to the MP4 file
        caption: Caption for the video / message
        bot_token: Telegram bot token (defaults to AVS_TG_BOT_TOKEN env var)
        chat_id: Telegram chat ID (defaults to AVS_TG_CHAT_ID env var)

    Returns:
        (ok, message) — message is the Telegram message URL on success, or error on failure
    """
    bot_token = bot_token or os.environ.get("AVS_TG_BOT_TOKEN")
    chat_id = chat_id or os.environ.get("AVS_TG_CHAT_ID")

    if not bot_token:
        return False, "AVS_TG_BOT_TOKEN env var not set"
    if not chat_id:
        return False, "AVS_TG_CHAT_ID env var not set"

    video_path = Path(video_path)
    if not video_path.exists():
        return False, f"video file not found: {video_path}"

    file_size = video_path.stat().st_size
    if file_size == 0:
        return False, "video file is empty"

    # Branch: small files go via sendVideo; large files go via storage.to
    if file_size <= TELEGRAM_BOT_API_LIMIT:
        return await _send_inline_video(video_path, caption, bot_token, chat_id)
    else:
        return await _send_via_storage_to(video_path, caption, bot_token, chat_id)


async def _send_inline_video(video_path: Path, caption: str,
                              bot_token: str, chat_id: str) -> tuple[bool, str]:
    """Send video as Telegram inline playback (file <= 50 MB)."""
    url = f"{TELEGRAM_API}/bot{bot_token}/sendVideo"
    safe_caption = (caption or "")[:1024]
    files = {"video": (video_path.name, open(video_path, "rb"), "video/mp4")}
    data = {
        "chat_id": chat_id,
        "caption": safe_caption,
        "parse_mode": "HTML",
        "supports_streaming": "true",
    }
    try:
        async with httpx.AsyncClient(timeout=600.0) as client:
            r = await client.post(url, data=data, files=files)
        if r.status_code != 200:
            return False, f"Telegram sendVideo HTTP {r.status_code}: {r.text[:300]}"
        response = r.json()
        if not response.get("ok"):
            return False, f"Telegram API error: {response.get('description', 'unknown')}"
        return _format_tg_success(response, file_size=video_path.stat().st_size, kind="video")
    except Exception as e:
        return False, f"exception: {e}"
    finally:
        try:
            files["video"][1].close()
        except Exception:
            pass


async def _send_via_storage_to(video_path: Path, caption: str,
                                bot_token: str, chat_id: str) -> tuple[bool, str]:
    """Upload video to storage.to (full quality), then send a Telegram message with the link."""
    from avs.utils.storage_to import upload_to_storage_to

    file_size = video_path.stat().st_size
    # Step 1: Upload to storage.to
    ok, msg, info = await upload_to_storage_to(video_path, timeout=1800.0)
    if not ok:
        return False, f"storage.to upload failed: {msg}"
    share_url = info.get("url", "")
    if not share_url:
        return False, "storage.to upload returned no URL"

    # Step 2: Send a text message with the download link
    file_id = info.get("file_id", "")
    expires = info.get("expires_at", "")
    safe_caption = (caption or "")[:1500]
    message = (
        f"🎬 {safe_caption}\n\n"
        f"⬇️ Download (full quality, {info.get('human_size', f'{file_size/1048576:.1f} MB')}):\n"
        f"{share_url}\n\n"
        f"📁 File ID: {file_id}\n"
        f"⏰ Expires: {expires.split('T')[0] if 'T' in expires else expires}\n"
        f"(storage.to free tier — 3 day expiry)"
    )
    url = f"{TELEGRAM_API}/bot{bot_token}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(url, json={
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "HTML",
                "disable_web_page_preview": False,
            })
        if r.status_code != 200:
            return False, f"Telegram sendMessage HTTP {r.status_code}: {r.text[:300]}"
        response = r.json()
        if not response.get("ok"):
            return False, f"Telegram API error: {response.get('description', 'unknown')}"
        return _format_tg_success(response, file_size=file_size, kind="storage.to_link")
    except Exception as e:
        return False, f"exception: {e}"


def _format_tg_success(response: dict, *, file_size: int, kind: str) -> tuple[bool, str]:
    """Format a successful Telegram response into a (ok, message) tuple."""
    result = response.get("result", {})
    message_id = result.get("message_id", 0)
    chat_info = result.get("chat", {})
    chat_username = chat_info.get("username", "")
    if chat_username:
        msg_url = f"https://t.me/{chat_username}/{message_id}"
    else:
        chat_id_str = str(chat_info.get("id", ""))
        # Strip the -100 prefix from supergroup IDs
        if chat_id_str.startswith("-100"):
            chat_id_str = chat_id_str[4:]
        msg_url = f"https://t.me/c/{chat_id_str}/{message_id}"
    return True, f"{kind} sent — {msg_url} ({file_size / 1048576:.1f} MB)"
