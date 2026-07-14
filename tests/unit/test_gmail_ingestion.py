"""
Tests for Phase 2 email ingestion:
  - GmailClient MIME parsing (pure unit, no network)
  - BlinkitEmailAdapter.is_po_email filter
  - ingest_label workflow (mocked Gmail + mocked DB)
"""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.adapters.email.base import AttachmentMeta, InboundEmail
from app.adapters.email.blinkit_email import BlinkitEmailAdapter
from app.adapters.email.gmail_client import GmailClient

# Import the workflow module so patch.object can reference it.
# Must be imported here (not inside tests) to ensure it appears as an attribute
# of its parent package before any patch.object calls.
import app.workflows.ingest_to_canonical as _wf  # noqa: E402

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES_DIR / name).read_text())


def _make_inbound_email(**kwargs) -> InboundEmail:
    defaults = dict(
        message_id="18d3a1b2c3d4e5f6",
        thread_id="18d3a1b2c3d4e5f6",
        subject="Purchase Order #BL-2024-001234 from Blinkit",
        sender="no-reply@blinkit.com",
        received_at=datetime(2025, 1, 1, tzinfo=UTC),
        headers={
            "from": "no-reply@blinkit.com",
            "subject": "Purchase Order #BL-2024-001234 from Blinkit",
        },
        body_text="Please find attached your Purchase Order from Blinkit.",
        body_html=None,
        label_ids=["UNREAD"],
        attachments=[],
    )
    defaults.update(kwargs)
    return InboundEmail(**defaults)


# ── GmailClient._parse_message / _extract_parts ───────────────────────────────

class TestGmailClientParsing:
    """Pure unit tests for MIME parsing — no network, no DB, no real credentials."""

    def _make_client(self) -> GmailClient:
        return GmailClient(credentials_path="x", token_path="y")

    def test_parse_message_happy_path(self):
        raw = _load_fixture("gmail_message_po.json")
        client = self._make_client()
        email = client._parse_message(raw)

        assert email.message_id == "18d3a1b2c3d4e5f6"
        assert email.thread_id == "18d3a1b2c3d4e5f6"
        assert email.subject == "Purchase Order #BL-2024-001234 from Blinkit"
        assert email.sender == "no-reply@blinkit.com"
        assert email.received_at.tzinfo is not None
        assert email.received_at.year == 2025

    def test_parse_message_extracts_body_text(self):
        raw = _load_fixture("gmail_message_po.json")
        client = self._make_client()
        email = client._parse_message(raw)

        assert email.body_text is not None
        assert "Purchase Order" in email.body_text

    def test_parse_message_extracts_pdf_attachment(self):
        raw = _load_fixture("gmail_message_po.json")
        client = self._make_client()
        email = client._parse_message(raw)

        assert len(email.attachments) == 1
        att = email.attachments[0]
        assert att.filename == "PO_BL-2024-001234.pdf"
        assert att.mime_type == "application/pdf"
        assert att.attachment_id == "ANGjdJ8a9b0c1d2e3f4g5h6i7j8k9l"
        assert att.size_bytes == 45231

    def test_parse_message_nonpo_no_attachments(self):
        raw = _load_fixture("gmail_message_nonpo.json")
        client = self._make_client()
        email = client._parse_message(raw)

        assert len(email.attachments) == 0
        assert email.body_text is not None

    def test_parse_message_headers_are_lowercased(self):
        raw = _load_fixture("gmail_message_po.json")
        client = self._make_client()
        email = client._parse_message(raw)

        assert "from" in email.headers
        assert "subject" in email.headers
        assert "From" not in email.headers

    def test_received_at_derived_from_date_header(self):
        raw = _load_fixture("gmail_message_po.json")
        client = self._make_client()
        email = client._parse_message(raw)

        # Date header: "Wed, 01 Jan 2025 00:00:00 +0000"
        assert email.received_at == datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC)

    def test_internal_date_fallback_when_no_date_header(self):
        raw = _load_fixture("gmail_message_po.json")
        # Remove the Date header to force internalDate fallback
        raw["payload"]["headers"] = [
            h for h in raw["payload"]["headers"] if h["name"] != "Date"
        ]
        client = self._make_client()
        email = client._parse_message(raw)

        # internalDate = 1735689600000 ms = 2025-01-01 00:00:00 UTC
        assert email.received_at.year == 2025
        assert email.received_at.tzinfo is not None


# ── BlinkitEmailAdapter.is_po_email ──────────────────────────────────────────

class TestBlinkitEmailAdapter:
    def setup_method(self):
        self.adapter = BlinkitEmailAdapter()

    def test_accepts_po_from_blinkit_domain(self):
        email = _make_inbound_email(sender="orders@blinkit.com", subject="Weekly report")
        assert self.adapter.is_po_email(email) is True

    def test_accepts_po_from_grofers_legacy_domain(self):
        email = _make_inbound_email(sender="no-reply@grofers.com", subject="Dispatch")
        assert self.adapter.is_po_email(email) is True

    def test_accepts_by_po_subject_keyword(self):
        email = _make_inbound_email(
            sender="unknown@example.com",
            subject="Purchase Order #BL12345 for Let's Try Foods",
        )
        assert self.adapter.is_po_email(email) is True

    def test_accepts_by_po_hash_keyword(self):
        email = _make_inbound_email(sender="ops@somedomain.com", subject="PO #12345 approval")
        assert self.adapter.is_po_email(email) is True

    def test_accepts_by_pdf_attachment(self):
        att = AttachmentMeta(
            filename="order.pdf",
            mime_type="application/pdf",
            size_bytes=1024,
            part_id="1",
            attachment_id="att1",
        )
        email = _make_inbound_email(sender="vendor@random.com", subject="Please review", attachments=[att])
        assert self.adapter.is_po_email(email) is True

    def test_rejects_newsletter_no_pdf_no_po_subject(self):
        email = _make_inbound_email(sender="newsletter@random.com", subject="Your weekly digest")
        assert self.adapter.is_po_email(email) is False

    def test_adapter_codes(self):
        assert self.adapter.get_partner_code() == "BLINKIT"
        assert self.adapter.get_gmail_label() == "BLINKIT_PO"


# ── ingest_label workflow (mocked) ───────────────────────────────────────────

class TestIngestLabelWorkflow:
    """
    Tests for ingest_to_canonical.ingest_label.
    Patches: GmailClient (no network) and SyncSessionLocal (no DB).
    Uses patch.object on the already-imported _wf module to avoid Python 3.14
    pkgutil.resolve_name issues with submodule dotted paths.
    """

    def _make_partner(self, code: str = "BLINKIT") -> MagicMock:
        partner = MagicMock()
        partner.id = uuid.uuid4()
        partner.code = code
        return partner

    def _make_session(self, side_effects: list) -> MagicMock:
        session = MagicMock()
        session.__enter__ = MagicMock(return_value=session)
        session.__exit__ = MagicMock(return_value=False)
        # ingest_to_canonical now uses session.execute(...).scalar_one_or_none()
        session.execute.return_value.scalar_one_or_none.side_effect = side_effects
        return session

    def test_saves_new_message(self, tmp_path):
        partner = self._make_partner()
        att = AttachmentMeta(
            filename="PO.pdf", mime_type="application/pdf",
            size_bytes=24, part_id="1", attachment_id="att1",
        )
        email = _make_inbound_email(attachments=[att])
        pdf_bytes = b"%PDF-1.4 fake"

        session = self._make_session([partner, None])  # partner found; no duplicate
        gmail = MagicMock()
        gmail.list_message_ids.return_value = [email.message_id]
        gmail.get_message.return_value = email
        gmail.download_attachment.return_value = pdf_bytes

        with (
            patch.object(_wf, "SyncSessionLocal", return_value=session),
            patch.object(_wf, "GmailClient", return_value=gmail),
            patch.object(_wf, "settings") as mock_settings,
        ):
            mock_settings.gmail_credentials_path = "x"
            mock_settings.gmail_token_path = "y"
            mock_settings.attachment_base_path = str(tmp_path)

            result = _wf.ingest_label("BLINKIT", "BLINKIT_PO")

        assert result.saved == 1
        assert result.skipped_duplicate == 0
        assert result.errors == []
        session.add.assert_called_once()

    def test_skips_duplicate_message(self, tmp_path):
        partner = self._make_partner()
        existing_raw = MagicMock()

        session = self._make_session([partner, existing_raw])  # duplicate found
        gmail = MagicMock()
        gmail.list_message_ids.return_value = ["18d3a1b2c3d4e5f6"]

        with (
            patch.object(_wf, "SyncSessionLocal", return_value=session),
            patch.object(_wf, "GmailClient", return_value=gmail),
            patch.object(_wf, "settings") as mock_settings,
        ):
            mock_settings.gmail_credentials_path = "x"
            mock_settings.gmail_token_path = "y"
            mock_settings.attachment_base_path = str(tmp_path)

            result = _wf.ingest_label("BLINKIT", "BLINKIT_PO")

        assert result.saved == 0
        assert result.skipped_duplicate == 1
        # get_message must NOT be called — short-circuits after duplicate pre-check
        gmail.get_message.assert_not_called()

    def test_unknown_partner_returns_error(self, tmp_path):
        session = self._make_session([None])  # no partner in DB

        with (
            patch.object(_wf, "SyncSessionLocal", return_value=session),
            patch.object(_wf, "GmailClient", return_value=MagicMock()),
            patch.object(_wf, "settings") as mock_settings,
        ):
            mock_settings.gmail_credentials_path = "x"
            mock_settings.gmail_token_path = "y"
            mock_settings.attachment_base_path = str(tmp_path)

            result = _wf.ingest_label("UNKNOWN_PARTNER", "SOME_LABEL")

        assert result.saved == 0
        assert len(result.errors) == 1
        assert "UNKNOWN_PARTNER" in result.errors[0]

    def test_non_po_email_is_filtered(self, tmp_path):
        partner = self._make_partner()
        newsletter = _make_inbound_email(
            sender="newsletter@random.com",
            subject="Your weekly digest",
            attachments=[],
        )
        session = self._make_session([partner, None])
        gmail = MagicMock()
        gmail.list_message_ids.return_value = [newsletter.message_id]
        gmail.get_message.return_value = newsletter

        with (
            patch.object(_wf, "SyncSessionLocal", return_value=session),
            patch.object(_wf, "GmailClient", return_value=gmail),
            patch.object(_wf, "settings") as mock_settings,
        ):
            mock_settings.gmail_credentials_path = "x"
            mock_settings.gmail_token_path = "y"
            mock_settings.attachment_base_path = str(tmp_path)

            result = _wf.ingest_label("BLINKIT", "BLINKIT_PO")

        assert result.saved == 0
        assert result.skipped_filter == 1
        session.add.assert_not_called()

    def test_attachment_saved_to_disk(self, tmp_path):
        partner = self._make_partner()
        att = AttachmentMeta(
            filename="PO_001.pdf", mime_type="application/pdf",
            size_bytes=8, part_id="1", attachment_id="att99",
        )
        email = _make_inbound_email(
            received_at=datetime(2025, 6, 15, tzinfo=UTC),
            attachments=[att],
        )
        pdf_bytes = b"%PDF-1.4"

        session = self._make_session([partner, None])
        gmail = MagicMock()
        gmail.list_message_ids.return_value = [email.message_id]
        gmail.get_message.return_value = email
        gmail.download_attachment.return_value = pdf_bytes

        with (
            patch.object(_wf, "SyncSessionLocal", return_value=session),
            patch.object(_wf, "GmailClient", return_value=gmail),
            patch.object(_wf, "settings") as mock_settings,
        ):
            mock_settings.gmail_credentials_path = "x"
            mock_settings.gmail_token_path = "y"
            mock_settings.attachment_base_path = str(tmp_path)

            result = _wf.ingest_label("BLINKIT", "BLINKIT_PO")

        expected = tmp_path / "BLINKIT" / "2025-06-15" / email.message_id / "PO_001.pdf"
        assert expected.exists(), f"Expected attachment at {expected}"
        assert expected.read_bytes() == pdf_bytes
        assert result.saved == 1
