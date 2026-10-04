"""storage.to uploader — no-signup anonymous file uploads up to 25 GB.

Three-step upload flow per https://storage.to/docs/api:
  1. POST /upload/init — get presigned PUT URLs (single <50MB, multipart >50MB)
  2. PUT bytes to the presigned URL(s) — bytes don't pass through storage.to servers
  3. POST /upload/confirm — finalize, get shareable URL

Anonymous uploads: no API key required, just a random X-Visitor-Token header.
Limits: 50 files / 24h, 100 GB upload bandwidth / 24h, files expire in 3 days.
"""
from __future__ import annotations

import os
import secrets
import json
from pathlib import Path
from typing import Any

import httpx

STORAGE_TO_API = "https://storage.to/api"
DEFAULT_VISITOR_TOKEN_FILE = Path.home() / ".config" / "storageto" / "token"


def get_or_create_visitor_token() -> str:
    """Generate or load a persistent visitor token (random string)."""
    if "AVS_STORAGE_TO_VISITOR_TOKEN" in os.environ:
        return os.environ["AVS_STORAGE_TO_VISITOR_TOKEN"]
    try:
        if DEFAULT_VISITOR_TOKEN_FILE.exists():
            return DEFAULT_VISITOR_TOKEN_FILE.read_text().strip()
    except OSError:
        pass
    # Generate a new random token
    token = secrets.token_urlsafe(32)
    try:
        DEFAULT_VISITOR_TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        DEFAULT_VISITOR_TOKEN_FILE.write_text(token)
    except OSError:
        pass
    return token


async def upload_to_storage_to(file_path: Path, *,
                                visitor_token: str | None = None,
                                timeout: float = 600.0) -> tuple[bool, str, dict[str, Any]]:
    """Upload a file to storage.to via the 3-step flow.

    Args:
        file_path: Path to the file
        visitor_token: Optional visitor token (auto-generated if not provided)
        timeout: HTTP timeout in seconds

    Returns:
        (ok, url_or_error, info_dict) — info_dict contains the file metadata on success
    """
    file_path = Path(file_path)
    if not file_path.exists():
        return False, f"file not found: {file_path}", {}

    file_size = file_path.stat().st_size
    if file_size == 0:
        return False, "file is empty", {}

    visitor_token = visitor_token or get_or_create_visitor_token()
    filename = file_path.name
    content_type = _guess_content_type(file_path)

    headers = {
        "X-Visitor-Token": visitor_token,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        # Step 1: Init upload
        init_url = f"{STORAGE_TO_API}/upload/init"
        init_body = {
            "filename": filename,
            "content_type": content_type,
            "size": file_size,
        }
        r = await client.post(init_url, json=init_body, headers=headers)
        if r.status_code != 200:
            return False, f"init failed: HTTP {r.status_code}: {r.text[:300]}", {}
        init_data = r.json()
        if not init_data.get("success"):
            return False, f"init failed: {init_data.get('error', 'unknown')}", {}

        r2_key = init_data.get("r2_key")
        owner_token = init_data.get("owner_token", "")
        if not r2_key:
            return False, "init response missing r2_key", {}

        upload_type = init_data.get("type", "single")

        if upload_type == "single":
            # Single PUT to presigned URL
            upload_url = init_data.get("upload_url")
            if not upload_url:
                return False, "init response missing upload_url", {}
            # Get any required headers
            put_headers = init_data.get("headers", {})
            with open(file_path, "rb") as f:
                file_data = f.read()
            r = await client.put(upload_url, content=file_data, headers=put_headers)
            if r.status_code not in (200, 201):
                return False, f"PUT failed: HTTP {r.status_code}: {r.text[:300]}", {}
        elif upload_type == "multipart":
            # Multipart upload
            upload_id = init_data.get("upload_id")
            part_size = init_data.get("part_size", 33554432)
            total_parts = init_data.get("total_parts", 0)
            initial_urls = init_data.get("initial_urls", {})
            if not upload_id or not initial_urls:
                return False, "multipart init missing fields", {}

            # Upload each part
            parts_info: list[dict[str, Any]] = []
            with open(file_path, "rb") as f:
                for part_num in range(1, total_parts + 1):
                    # Get URL for this part (from initial_urls or fetch more)
                    part_url = initial_urls.get(str(part_num))
                    if not part_url:
                        # Fetch more URLs
                        r = await client.post(
                            f"{STORAGE_TO_API}/upload/parts",
                            json={"upload_id": upload_id, "part_numbers": [part_num]},
                            headers=headers,
                        )
                        if r.status_code != 200:
                            return False, f"parts fetch failed for {part_num}: HTTP {r.status_code}", {}
                        urls_data = r.json()
                        for pu in urls_data.get("part_urls", []):
                            if pu.get("partNumber") == part_num:
                                part_url = pu.get("url")
                                break
                    if not part_url:
                        return False, f"no URL for part {part_num}", {}

                    # Read part bytes
                    part_bytes = f.read(part_size)
                    if not part_bytes:
                        break

                    # PUT part to presigned URL
                    r = await client.put(part_url, content=part_bytes)
                    if r.status_code not in (200, 201):
                        return False, f"PUT part {part_num} failed: HTTP {r.status_code}: {r.text[:300]}", {}
                    etag = r.headers.get("etag", "")
                    parts_info.append({"partNumber": part_num, "etag": etag})

            # Complete multipart
            r = await client.post(
                f"{STORAGE_TO_API}/upload/complete-multipart",
                json={"upload_id": upload_id, "parts": parts_info},
                headers={**headers, "X-Owner-Token": owner_token},
            )
            if r.status_code != 200:
                return False, f"complete-multipart failed: HTTP {r.status_code}: {r.text[:300]}", {}
        else:
            return False, f"unknown upload type: {upload_type}", {}

        # Step 3: Confirm
        confirm_url = f"{STORAGE_TO_API}/upload/confirm"
        confirm_body = {
            "filename": filename,
            "size": file_size,
            "content_type": content_type,
            "r2_key": r2_key,
        }
        r = await client.post(confirm_url, json=confirm_body, headers={
            **headers, "X-Owner-Token": owner_token,
        })
        if r.status_code != 200:
            return False, f"confirm failed: HTTP {r.status_code}: {r.text[:300]}", {}
        confirm_data = r.json()
        if not confirm_data.get("success"):
            return False, f"confirm failed: {confirm_data.get('error', 'unknown')}", {}

        file_info = confirm_data.get("file", {})
        share_url = file_info.get("url", "")
        if not share_url:
            return False, "confirm response missing url", {}

        return True, share_url, {
            "url": share_url,
            "file_id": file_info.get("id"),
            "filename": file_info.get("filename"),
            "size_bytes": file_info.get("size"),
            "human_size": file_info.get("human_size"),
            "expires_at": file_info.get("expires_at"),
            "owner_token": confirm_data.get("owner_token", ""),
        }


def _guess_content_type(path: Path) -> str:
    """Guess MIME type from extension."""
    ext = path.suffix.lower()
    return {
        ".mp4": "video/mp4",
        ".mkv": "video/x-matroska",
        ".mov": "video/quicktime",
        ".webm": "video/webm",
        ".avi": "video/x-msvideo",
        ".mp3": "audio/mpeg",
        ".wav": "audio/wav",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".pdf": "application/pdf",
        ".zip": "application/zip",
        ".txt": "text/plain",
        ".json": "application/json",
    }.get(ext, "application/octet-stream")
