"""Telegram Bot API client — sends final video to a Telegram chat.

Uses multipart/form-data upload via the Telegram Bot API:
  POST https://api.telegram.org/bot<token>/sendVideo
  form-data: chat_id=<chat_id>, video=<file>, caption=<caption>

Telegram limits:
  - Bot API: max 50 MB file via standard upload
  - Bot API with local bot API server: max 2 GB
  - For > 50 MB files via standard upload: must use the sendDocument endpoint
    (or chunk via the local Bot API server, not supported here)

If the file is > 50 MB, we fall back to sendDocument (sends as a file attachment
instead of a video attachment — loses inline playback but at least delivers).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx

TELEGRAM_API = "https://api.telegram.org"


async def send_video_to_telegram(video_path: Path, caption: str = "",
                                  bot_token: str | None = None,
                                  chat_id: str | None = None) -> tuple[bool, str]:
    """Send a video file to a Telegram chat via Bot API.

    Args:
        video_path: Path to the MP4 file
        caption: Caption for the video
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

    # Telegram Bot API limit: 50 MB for sendVideo via standard upload
    use_document = file_size > 50 * 1024 * 1024
    endpoint = "sendDocument" if use_document else "sendVideo"
    field_name = "document" if use_document else "video"

    url = f"{TELEGRAM_API}/bot{bot_token}/{endpoint}"

    # Truncate caption to 1024 chars (Telegram limit)
    safe_caption = caption[:1024] if caption else ""

    # Prepare multipart form data
    files = {field_name: (video_path.name, open(video_path, "rb"), "video/mp4")}
    data = {
        "chat_id": chat_id,
        "caption": safe_caption,
        "parse_mode": "HTML",
    }
    if not use_document:
        # For videos, also specify duration if known
        data["supports_streaming"] = "true"

    try:
        async with httpx.AsyncClient(timeout=600.0) as client:
            r = await client.post(url, data=data, files=files)
        if r.status_code != 200:
            return False, f"Telegram API HTTP {r.status_code}: {r.text[:300]}"
        response = r.json()
        if not response.get("ok"):
            return False, f"Telegram API error: {response.get('description', 'unknown')}"

        # Get message ID and chat info for the URL
        result = response.get("result", {})
        message_id = result.get("message_id", 0)
        chat_info = result.get("chat", {})
        chat_type = chat_info.get("type", "private")
        chat_username = chat_info.get("username", "")
        # Construct the message URL
        if chat_username:
            msg_url = f"https://t.me/{chat_username}/{message_id}"
        else:
            msg_url = f"https://t.me/c/{chat_info.get('id', '').replace('-100', '')}/{message_id}"

        kind = "document" if use_document else "video"
        return True, f"{kind} sent — {msg_url} ({file_size / 1048576:.1f} MB)"
    except Exception as e:
        return False, f"exception: {e}"
    finally:
        try:
            files[field_name][1].close()
        except Exception:
            pass
