"""
Email outbound adapter — sends formatted ACK/ASN notifications via Gmail.

Used for email-based partners (Swiggy, BigBasket, DMart, etc.) that receive
outbound documents as email replies rather than via API.

Requires: Gmail credentials with the `gmail.send` scope (in addition to
the `gmail.readonly` scope used for ingest). Re-authorize with auth_gmail.py
after adding the send scope.

Payload schema (built by b1_to_outbound.py):
  {
    "to": "ops@partner.com",
    "subject": "PO Acknowledgement / ASN / Invoice",
    "body_text": "Plain-text body",
    "body_html": "<html>...</html>",    (optional)
    "reply_to_message_id": "..."         (optional Gmail message_id to thread)
  }
"""
from __future__ import annotations

import base64
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

import structlog

from app.adapters.outbound.base import BaseOutboundAdapter, OutboundResult

log = structlog.get_logger(__name__)

_GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"


class EmailOutboundAdapter(BaseOutboundAdapter):
    """Sends outbound documents as emails using Gmail API."""

    @property
    def channel(self) -> str:
        return "EMAIL"

    def send(
        self,
        doc_type: str,
        payload: dict[str, Any],
        idempotency_key: str,
    ) -> OutboundResult:
        try:
            return self._send_email(payload)
        except Exception as exc:
            log.error("email_outbound.send_failed", doc_type=doc_type, error=str(exc))
            return OutboundResult(success=False, error=str(exc))

    def _send_email(self, payload: dict[str, Any]) -> OutboundResult:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        from app.config import get_settings

        settings = get_settings()
        creds = Credentials.from_authorized_user_file(
            settings.gmail_token_path,
            scopes=[_GMAIL_SEND_SCOPE],
        )
        service = build("gmail", "v1", credentials=creds, cache_discovery=False)

        msg = self._build_mime(payload)
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
        body: dict[str, Any] = {"raw": raw}

        reply_to_id = payload.get("reply_to_message_id")
        if reply_to_id:
            body["threadId"] = self._get_thread_id(service, reply_to_id)

        sent = service.users().messages().send(userId="me", body=body).execute()
        message_id: str = sent.get("id", "")
        log.info("email_outbound.sent", to=payload.get("to"), message_id=message_id)
        return OutboundResult(success=True, external_ref=message_id)

    @staticmethod
    def _build_mime(payload: dict[str, Any]) -> MIMEMultipart:
        msg = MIMEMultipart("alternative")
        msg["To"] = payload.get("to", "")
        msg["Subject"] = payload.get("subject", "(no subject)")
        if payload.get("body_text"):
            msg.attach(MIMEText(payload["body_text"], "plain", "utf-8"))
        if payload.get("body_html"):
            msg.attach(MIMEText(payload["body_html"], "html", "utf-8"))
        return msg

    @staticmethod
    def _get_thread_id(service: Any, message_id: str) -> str | None:
        try:
            msg = service.users().messages().get(
                userId="me", id=message_id, format="minimal"
            ).execute()
            return msg.get("threadId")
        except Exception:
            return None
