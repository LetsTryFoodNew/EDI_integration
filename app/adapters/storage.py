"""
Cloudinary storage adapter for raw attachment files (PDF, Excel, etc.).

Files are uploaded as 'raw' resource type under the path:
    edi/{partner_code}/{date}/{message_id}/{filename}

Falls back to local disk when CLOUDINARY_CLOUD_NAME is not configured
(useful for local dev without Cloudinary credentials).

Requires in .env:
    CLOUDINARY_CLOUD_NAME=...
    CLOUDINARY_API_KEY=...
    CLOUDINARY_API_SECRET=...
"""
from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import structlog

from app.config import get_settings

log = structlog.get_logger(__name__)

_configured = False


def _ensure_cloudinary_configured() -> None:
    global _configured  # noqa: PLW0603
    if not _configured:
        import cloudinary

        s = get_settings()
        cloudinary.config(
            cloud_name=s.cloudinary_cloud_name,
            api_key=s.cloudinary_api_key,
            api_secret=s.cloudinary_api_secret,
            secure=True,
        )
        _configured = True


def upload_attachment(
    data: bytes,
    filename: str,
    partner_code: str,
    message_id: str,
    date_str: str,
    mime_type: str = "application/octet-stream",
) -> dict[str, Any]:
    """
    Upload one attachment to Cloudinary as a raw file.

    Returns a dict matching the RawMessage.attachment_paths schema:
        filename, url, public_id, mime_type, size_bytes

    If Cloudinary is not configured, saves to local disk and returns a
    file:// URL so existing code keeps working in dev.
    """
    settings = get_settings()

    if not settings.cloudinary_cloud_name:
        return _save_local(data, filename, partner_code, message_id, date_str, mime_type, settings)

    return _upload_cloudinary(data, filename, partner_code, message_id, date_str, mime_type)


def _upload_cloudinary(
    data: bytes,
    filename: str,
    partner_code: str,
    message_id: str,
    date_str: str,
    mime_type: str,
) -> dict[str, Any]:
    import cloudinary.uploader

    _ensure_cloudinary_configured()

    # Build a stable public_id — Cloudinary appends the extension for raw resources.
    safe_name = filename.replace(" ", "_")
    public_id = f"edi/{partner_code}/{date_str}/{message_id}/{safe_name}"

    result: dict[str, Any] = cloudinary.uploader.upload(
        io.BytesIO(data),
        resource_type="raw",
        public_id=public_id,
        overwrite=False,      # idempotent: same message_id/filename → same public_id
        use_filename=True,
        unique_filename=False,
    )

    log.debug(
        "storage.uploaded",
        provider="cloudinary",
        public_id=result["public_id"],
        bytes=len(data),
    )
    return {
        "filename": filename,
        "url": result["secure_url"],
        "public_id": result["public_id"],
        "mime_type": mime_type,
        "size_bytes": len(data),
    }


def _save_local(
    data: bytes,
    filename: str,
    partner_code: str,
    message_id: str,
    date_str: str,
    mime_type: str,
    settings: Any,
) -> dict[str, Any]:
    """Local disk fallback for dev environments without Cloudinary credentials."""
    base = Path(settings.attachment_base_path) / partner_code / date_str / message_id
    base.mkdir(parents=True, exist_ok=True)
    dest = base / filename
    dest.write_bytes(data)

    log.debug("storage.saved_local", path=str(dest))
    return {
        "filename": filename,
        "url": dest.as_uri(),
        "public_id": str(dest),
        "mime_type": mime_type,
        "size_bytes": len(data),
    }
