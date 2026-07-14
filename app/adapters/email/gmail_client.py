"""
Gmail API client — OAuth2 token management, message listing, body parsing,
attachment download.

Known quirks:
  - Gmail message bodies are base64url-encoded.
  - Multipart messages need recursive part traversal.
  - Attachments > ~25 MB arrive with a separate attachmentId that must be
    fetched in a second call; smaller attachments embed data inline in the part.
  - The 'internalDate' field is epoch-milliseconds (not seconds).
  - Label IDs are opaque strings; names must be resolved via labels.list().

Setup: run `python scripts/auth_gmail.py` once to produce token.json.
"""
from __future__ import annotations

import base64
import contextlib
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

import structlog
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from app.adapters.email.base import AttachmentMeta, InboundEmail

log = structlog.get_logger(__name__)

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


class GmailAuthError(RuntimeError):
    """Raised when no valid token.json exists — run auth_gmail.py first."""


class GmailClient:
    """
    Thin wrapper around the Gmail API v1.
    Thread-safety: one instance per process is fine; the underlying HTTP
    transport is not thread-safe so do not share across threads.
    """

    def __init__(self, credentials_path: str, token_path: str) -> None:
        self._credentials_path = Path(credentials_path)
        self._token_path = Path(token_path)
        self._service: Any = None
        self._label_cache: dict[str, str] = {}   # name → id

    # ── Authentication ────────────────────────────────────────────────────────

    def _load_credentials(self) -> Credentials:
        if not self._token_path.exists():
            raise GmailAuthError(
                f"No token found at {self._token_path}. "
                "Run `python scripts/auth_gmail.py` to authorise."
            )

        creds = Credentials.from_authorized_user_file(str(self._token_path), SCOPES)

        if not creds.valid:
            if creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                    self._save_token(creds)
                except RefreshError as exc:
                    raise GmailAuthError(
                        "Token refresh failed. Re-run `python scripts/auth_gmail.py`."
                    ) from exc
            else:
                raise GmailAuthError(
                    "Token is invalid and cannot be refreshed. "
                    "Re-run `python scripts/auth_gmail.py`."
                )

        return creds

    def _save_token(self, creds: Credentials) -> None:
        self._token_path.parent.mkdir(parents=True, exist_ok=True)
        self._token_path.write_text(creds.to_json())

    def _get_service(self) -> Any:
        if self._service is None:
            creds = self._load_credentials()
            self._service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        return self._service

    # ── Label resolution ──────────────────────────────────────────────────────

    def get_label_id(self, label_name: str) -> str | None:
        """Resolve a label name to its ID; returns None if not found."""
        if label_name in self._label_cache:
            return self._label_cache[label_name]

        service = self._get_service()
        try:
            result = service.users().labels().list(userId="me").execute()
        except HttpError as exc:
            log.error("gmail.labels.list failed", error=str(exc))
            raise

        for label in result.get("labels", []):
            self._label_cache[label["name"]] = label["id"]

        return self._label_cache.get(label_name)

    # ── Message listing ───────────────────────────────────────────────────────

    def list_message_ids(self, label_name: str, max_results: int = 100) -> list[str]:
        """
        Return Gmail message IDs in the given label, newest first.
        Returns empty list if label doesn't exist.
        """
        label_id = self.get_label_id(label_name)
        if label_id is None:
            log.warning("gmail.label_not_found", label_name=label_name)
            return []

        service = self._get_service()
        ids: list[str] = []
        page_token: str | None = None

        while True:
            kwargs: dict[str, Any] = {
                "userId": "me",
                "labelIds": [label_id],
                "maxResults": min(max_results - len(ids), 100),
            }
            if page_token:
                kwargs["pageToken"] = page_token

            try:
                result = service.users().messages().list(**kwargs).execute()
            except HttpError as exc:
                log.error("gmail.messages.list failed", label=label_name, error=str(exc))
                raise

            for msg in result.get("messages", []):
                ids.append(msg["id"])

            if len(ids) >= max_results:
                break
            page_token = result.get("nextPageToken")
            if not page_token:
                break

        return ids

    # ── Message parsing ───────────────────────────────────────────────────────

    def get_message(self, message_id: str) -> InboundEmail:
        """Fetch a full Gmail message and return a structured InboundEmail."""
        service = self._get_service()
        try:
            raw = service.users().messages().get(
                userId="me", id=message_id, format="full"
            ).execute()
        except HttpError as exc:
            log.error("gmail.messages.get failed", message_id=message_id, error=str(exc))
            raise

        return self._parse_message(raw)

    def _parse_message(self, raw: dict[str, Any]) -> InboundEmail:
        message_id: str = raw["id"]
        thread_id: str = raw["threadId"]
        label_ids: list[str] = raw.get("labelIds", [])

        # internalDate is epoch-milliseconds
        received_at = datetime.fromtimestamp(int(raw["internalDate"]) / 1000, tz=UTC)

        payload = raw.get("payload", {})
        headers = {h["name"].lower(): h["value"] for h in payload.get("headers", [])}

        subject = headers.get("subject", "")
        sender = headers.get("from", "")

        # Prefer the Date header if present (more accurate than internalDate)
        if "date" in headers:
            with contextlib.suppress(Exception):
                received_at = parsedate_to_datetime(headers["date"]).astimezone(UTC)

        body_text: str | None = None
        body_html: str | None = None
        attachments: list[AttachmentMeta] = []

        self._traverse_parts(payload, body_container=[None, None, attachments])
        # unpack via a mutable trick
        parts_result: list[Any] = [None, None, attachments]
        self._extract_parts(payload, parts_result)
        body_text, body_html = parts_result[0], parts_result[1]

        return InboundEmail(
            message_id=message_id,
            thread_id=thread_id,
            subject=subject,
            sender=sender,
            received_at=received_at,
            headers=headers,
            body_text=body_text,
            body_html=body_html,
            label_ids=label_ids,
            attachments=attachments,
        )

    def _traverse_parts(
        self,
        part: dict[str, Any],
        body_container: list[Any],
    ) -> None:
        """Unused — replaced by _extract_parts. Kept to avoid confusion."""

    def _extract_parts(
        self,
        part: dict[str, Any],
        result: list[Any],  # [body_text, body_html, attachments]
    ) -> None:
        """Recursively walk MIME parts to extract body and attachment metadata."""
        mime_type: str = part.get("mimeType", "")
        filename: str = part.get("filename", "")
        body: dict[str, Any] = part.get("body", {})
        part_id: str = part.get("partId", "")

        if filename:
            # This is an attachment
            result[2].append(AttachmentMeta(
                filename=filename,
                mime_type=mime_type,
                size_bytes=body.get("size", 0),
                part_id=part_id,
                attachment_id=body.get("attachmentId"),
            ))
            return

        if mime_type in ("text/plain", "text/html") and not part.get("parts"):
            data = body.get("data", "")
            if data:
                try:
                    decoded = base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
                except Exception:
                    decoded = ""
                if mime_type == "text/plain" and result[0] is None:
                    result[0] = decoded
                elif mime_type == "text/html" and result[1] is None:
                    result[1] = decoded
            return

        for sub_part in part.get("parts", []):
            self._extract_parts(sub_part, result)

    # ── Attachment download ───────────────────────────────────────────────────

    def download_attachment(self, message_id: str, attachment_id: str) -> bytes:
        """Download and decode one attachment. Returns raw bytes."""
        service = self._get_service()
        try:
            att = service.users().messages().attachments().get(
                userId="me", messageId=message_id, id=attachment_id
            ).execute()
        except HttpError as exc:
            log.error(
                "gmail.attachments.get failed",
                message_id=message_id,
                attachment_id=attachment_id,
                error=str(exc),
            )
            raise

        data = att.get("data", "")
        return base64.urlsafe_b64decode(data + "==")
