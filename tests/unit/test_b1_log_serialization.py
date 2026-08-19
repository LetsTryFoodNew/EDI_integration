"""
Regression tests for B1ApiLog → API serialization.

These exist because a whole surface — PO detail's push history, the B1 Logs list, its
detail view, and the `success` filter — referenced attributes B1ApiLog does not have:
`http_status` (the column is `response_status`), `request_payload`/`response_payload`
(`request_body`/`response_body`), and `success` (no column at all).

None of it failed until a PO was actually pushed to SAP. Until then the table was empty,
so the list endpoint serialized zero rows and looked healthy, and PO detail never entered
the loop. The first real Sales Order turned every one of those into a 500.

So the point of these tests is to exercise the mapping against a *populated* log row
rather than an empty list.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from app.models.b1_log import B1ApiLog
from app.schemas.api import B1LogDetail, B1LogListItem, B1PushHistoryItem


def _log(**kw) -> B1ApiLog:
    base = dict(
        id=uuid.uuid4(),
        po_id=uuid.uuid4(),
        operation="create_sales_order",
        http_method="POST",
        endpoint="/b1s/v2/Orders",
        request_body={"CardCode": "D00086"},
        response_status=201,
        response_body={"DocEntry": 1771, "DocNum": 3000046},
        duration_ms=1200,
        error_code=None,
        error_message=None,
        created_at=datetime.now(UTC),
    )
    base.update(kw)
    return B1ApiLog(**base)


class TestSuccessIsDerived:
    """
    There is no `success` column on purpose — it would be a second source of truth that
    could drift from response_status. It is computed from the HTTP status instead.
    """

    @pytest.mark.parametrize(
        ("status", "expected"),
        [(200, True), (201, True), (204, True), (299, True),
         (300, False), (400, False), (401, False), (500, False),
         (0, False), (None, False)],
    )
    def test_from_http_status(self, status: int | None, expected: bool) -> None:
        assert _log(response_status=status).success is expected

    def test_network_failure_is_not_a_success(self) -> None:
        """A request that never reached B1 is logged with status 0."""
        entry = _log(response_status=0, response_body=None,
                     error_message="Network error calling B1 create_sales_order")
        assert entry.success is False

    def test_usable_as_a_sql_expression(self) -> None:
        """`GET /api/b1-logs?success=false` filters in SQL, not in Python."""
        clause = str(B1ApiLog.success.expression)
        assert "response_status" in clause


class TestApiFieldNames:
    """
    The API names differ from the column names. Each mapping is asserted explicitly so a
    rename on either side fails here rather than at the first populated response.
    """

    def test_push_history_item(self) -> None:
        entry = _log()
        item = B1PushHistoryItem(
            id=entry.id,
            http_method=entry.http_method,
            endpoint=entry.endpoint,
            http_status=entry.response_status,
            success=entry.success,
            error_code=entry.error_code,
            error_message=entry.error_message,
            duration_ms=entry.duration_ms,
            created_at=entry.created_at,
        )
        assert item.http_status == 201
        assert item.success is True

    def test_log_list_item(self) -> None:
        entry = _log(response_status=400, error_message="Invalid warehouse")
        item = B1LogListItem(
            id=entry.id, po_id=entry.po_id, http_method=entry.http_method,
            endpoint=entry.endpoint, http_status=entry.response_status,
            success=entry.success, error_code=entry.error_code,
            error_message=entry.error_message, duration_ms=entry.duration_ms,
            created_at=entry.created_at,
        )
        assert item.http_status == 400
        assert item.success is False
        assert item.error_message == "Invalid warehouse"

    def test_log_detail_carries_both_payloads(self) -> None:
        entry = _log()
        detail = B1LogDetail(
            id=entry.id, po_id=entry.po_id, http_method=entry.http_method,
            endpoint=entry.endpoint,
            request_payload=entry.request_body,      # column is request_body
            response_payload=entry.response_body,    # column is response_body
            http_status=entry.response_status,
            success=entry.success, error_code=entry.error_code,
            error_message=entry.error_message, duration_ms=entry.duration_ms,
            created_at=entry.created_at,
        )
        assert detail.request_payload == {"CardCode": "D00086"}
        assert detail.response_payload["DocNum"] == 3000046

    def test_the_columns_the_routes_read_all_exist(self) -> None:
        """
        The original failure was an AttributeError at response-build time. Reading every
        attribute the routes touch catches a rename without needing a live request.
        """
        entry = _log()
        for attr in (
            "id", "po_id", "http_method", "endpoint", "request_body", "response_body",
            "response_status", "duration_ms", "error_code", "error_message",
            "created_at", "success",
        ):
            getattr(entry, attr)

    def test_names_the_api_uses_are_not_columns(self) -> None:
        """
        Guards the reverse mistake: if someone later adds a real `http_status` column,
        the explicit mappings above become misleading and should be revisited.
        """
        columns = set(B1ApiLog.__table__.columns.keys())
        assert "http_status" not in columns
        assert "request_payload" not in columns
        assert "response_payload" not in columns
        assert "success" not in columns
