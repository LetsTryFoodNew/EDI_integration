"""
Unit tests for Branch Master (OBPL) and Warehouse Master (OWHS).

Two things carry real risk here and are pinned accordingly:

1. **Sync must never clobber an ops decision.** `is_active` and `notes` are the only
   locally-owned columns on these tables. If a SAP push overwrote them, an ops user who
   parked a warehouse would find it silently back in service on the next sync — and
   nothing in the response would say so.

2. **A warehouse must not be created against a branch that does not exist.** B1 rejects
   a marketing document whose warehouse and branch disagree, so a dangling link would
   surface much later as a Sales Order push failure rather than as a rejected sync row.

The Y/N coercion is pinned too, because SAP sends OBPL.Disabled and OWHS.Inactive as
NVARCHAR and a silent mis-read would invert a branch's status.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.schemas.api import (
    BranchMasterSyncItem,
    BranchMasterSyncRequest,
    BranchMasterUpdate,
    WarehouseMasterSyncItem,
    WarehouseMasterSyncRequest,
    WarehouseMasterUpdate,
)

# ── Fakes ─────────────────────────────────────────────────────────────────────

class _Result:
    def __init__(self, value: object) -> None:
        self._value = value

    def scalar_one_or_none(self) -> object:
        return self._value


class FakeSession:
    """
    Stands in for a Session for the two lookups the sync routes make — branch by
    `bpl_id`, warehouse by `whs_code`. Resolves the target from the statement's entity
    and its bound parameter, so the routes run against real `select()` objects.
    """

    def __init__(self, branches: list = (), warehouses: list = ()) -> None:
        self.branches = list(branches)
        self.warehouses = list(warehouses)
        self.added: list = []
        self.committed = False

    def execute(self, stmt: object) -> _Result:
        entity = stmt.column_descriptions[0]["entity"].__name__
        params = stmt.compile().params
        if entity == "BranchMaster":
            bpl_id = params.get("bpl_id_1")
            return _Result(next((b for b in self.branches if b.bpl_id == bpl_id), None))
        if entity == "WarehouseMaster":
            code = params.get("whs_code_1")
            return _Result(next((w for w in self.warehouses if w.whs_code == code), None))
        raise AssertionError(f"unexpected lookup on {entity}")

    def add(self, obj: object) -> None:
        self.added.append(obj)

    def flush(self) -> None:
        pass

    def commit(self) -> None:
        self.committed = True

    def get(self, model: object, pk: uuid.UUID) -> object | None:
        pool = self.branches if model.__name__ == "BranchMaster" else self.warehouses
        return next((r for r in pool if r.id == pk), None)


_NOW = datetime(2026, 8, 19, tzinfo=UTC)


def _user() -> SimpleNamespace:
    return SimpleNamespace(email="ops@letstryfoods.com")


def _branch_row(**kw) -> SimpleNamespace:
    base = dict(
        id=uuid.uuid4(), bpl_id=1, bpl_name="Mumbai", disabled=False, address=None,
        street=None, block=None, city="Mumbai", zip_code=None, state="Maharashtra",
        country="India", gstin=None, is_active=True, notes=None, deleted_at=None,
        created_at=_NOW, updated_at=_NOW,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _warehouse_row(**kw) -> SimpleNamespace:
    base = dict(
        id=uuid.uuid4(), whs_code="WH-01", whs_name="Mumbai Main", branch_id=uuid.uuid4(),
        inactive=False, location=None, street=None, block=None, city="Mumbai",
        zip_code=None, state="Maharashtra", country="India", is_active=True,
        notes=None, deleted_at=None, created_at=_NOW, updated_at=_NOW,
    )
    base.update(kw)
    return SimpleNamespace(**base)


# ── SAP's Y/N flags ───────────────────────────────────────────────────────────

class TestYesNoFlags:
    """OBPL.Disabled and OWHS.Inactive arrive as NVARCHAR — all three spellings work."""

    @pytest.mark.parametrize(
        ("sent", "expected"),
        [("Y", True), ("N", False), ("y", True), ("n", False),
         (True, True), (False, False), (1, True), (0, False)],
    )
    def test_branch_disabled_accepts_sap_spellings(self, sent: object, expected: bool) -> None:
        item = BranchMasterSyncItem(bpl_id=1, bpl_name="Mumbai", disabled=sent)
        assert item.disabled is expected

    @pytest.mark.parametrize(("sent", "expected"), [("Y", True), ("N", False)])
    def test_warehouse_inactive_accepts_sap_spellings(self, sent: object, expected: bool) -> None:
        item = WarehouseMasterSyncItem(whs_code="WH-01", whs_name="Main", bpl_id=1, inactive=sent)
        assert item.inactive is expected

    def test_flags_default_to_enabled(self) -> None:
        assert BranchMasterSyncItem(bpl_id=1, bpl_name="Mumbai").disabled is False
        assert WarehouseMasterSyncItem(whs_code="W", whs_name="N", bpl_id=1).inactive is False


# ── Schema contract ───────────────────────────────────────────────────────────

class TestSyncSchemaContract:
    def test_unknown_field_is_rejected(self) -> None:
        """A SAP-side typo must fail loudly, not be dropped into the void."""
        with pytest.raises(ValidationError):
            BranchMasterSyncItem(bpl_id=1, bpl_name="Mumbai", BPLId=1)

    def test_branch_requires_id_and_name(self) -> None:
        with pytest.raises(ValidationError):
            BranchMasterSyncItem(bpl_name="Mumbai")
        with pytest.raises(ValidationError):
            BranchMasterSyncItem(bpl_id=1, bpl_name="")

    def test_bpl_id_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            BranchMasterSyncItem(bpl_id=0, bpl_name="Mumbai")

    def test_batch_is_bounded(self) -> None:
        with pytest.raises(ValidationError):
            BranchMasterSyncRequest(branches=[])
        with pytest.raises(ValidationError):
            WarehouseMasterSyncRequest(warehouses=[])

    def test_get_response_can_be_posted_straight_back(self) -> None:
        """A row read from GET must validate as a sync item and as an update body."""
        branch = {
            "id": str(uuid.uuid4()), "bpl_id": 1, "bpl_name": "Mumbai", "disabled": False,
            "address": None, "street": None, "block": None, "city": "Mumbai",
            "zip_code": None, "state": "Maharashtra", "country": "India", "gstin": None,
            "is_active": True, "notes": "ops note", "warehouse_count": 3,
            "created_at": "2026-08-19T00:00:00Z", "updated_at": "2026-08-19T00:00:00Z",
        }
        assert BranchMasterSyncItem(**branch).bpl_id == 1
        assert BranchMasterUpdate(**branch).notes == "ops note"

        warehouse = {
            "id": str(uuid.uuid4()), "whs_code": "WH-01", "whs_name": "Main", "bpl_id": 1,
            "branch_name": "Mumbai", "inactive": False, "location": 1, "street": None,
            "block": None, "city": "Mumbai", "zip_code": None, "state": "Maharashtra",
            "country": "India", "is_active": True, "notes": None,
            "created_at": "2026-08-19T00:00:00Z", "updated_at": "2026-08-19T00:00:00Z",
        }
        assert WarehouseMasterSyncItem(**warehouse).whs_code == "WH-01"
        assert WarehouseMasterUpdate(**warehouse).whs_code == "WH-01"


# ── Sync: branches ────────────────────────────────────────────────────────────

class TestSyncBranches:
    def test_creates_a_new_branch(self) -> None:
        from app.api.routes.branch_warehouse import sync_branches

        db = FakeSession()
        result = sync_branches(
            BranchMasterSyncRequest(branches=[
                BranchMasterSyncItem(bpl_id=7, bpl_name="Delhi", disabled="N", state="Delhi"),
            ]),
            db=db, current_user=_user(),
        )
        assert (result.created, result.updated, result.skipped) == (1, 0, 0)
        branch = next(o for o in db.added if type(o).__name__ == "BranchMaster")
        assert (branch.bpl_id, branch.bpl_name, branch.state) == (7, "Delhi", "Delhi")
        assert db.committed

    def test_updates_sap_owned_fields_on_an_existing_branch(self) -> None:
        from app.api.routes.branch_warehouse import sync_branches

        existing = _branch_row(bpl_id=7, bpl_name="Delhi", disabled=False)
        db = FakeSession(branches=[existing])
        result = sync_branches(
            BranchMasterSyncRequest(branches=[
                BranchMasterSyncItem(bpl_id=7, bpl_name="Delhi NCR", disabled="Y"),
            ]),
            db=db, current_user=_user(),
        )
        assert (result.created, result.updated) == (0, 1)
        assert existing.bpl_name == "Delhi NCR"
        assert existing.disabled is True

    def test_sync_never_overwrites_ops_owned_fields(self) -> None:
        """The whole point of splitting is_active/notes out of SAP's field set."""
        from app.api.routes.branch_warehouse import sync_branches

        existing = _branch_row(bpl_id=7, is_active=False, notes="Parked by ops")
        db = FakeSession(branches=[existing])
        sync_branches(
            BranchMasterSyncRequest(branches=[
                BranchMasterSyncItem(
                    bpl_id=7, bpl_name="Delhi", is_active=True, notes="SAP clobber attempt",
                ),
            ]),
            db=db, current_user=_user(),
        )
        assert existing.is_active is False
        assert existing.notes == "Parked by ops"

    def test_soft_deleted_branch_is_skipped_and_reported(self) -> None:
        from app.api.routes.branch_warehouse import sync_branches

        db = FakeSession(branches=[_branch_row(bpl_id=7, deleted_at=datetime.now(UTC))])
        result = sync_branches(
            BranchMasterSyncRequest(branches=[BranchMasterSyncItem(bpl_id=7, bpl_name="Delhi")]),
            db=db, current_user=_user(),
        )
        assert result.skipped == 1
        assert "soft-deleted" in result.errors[0]


# ── Sync: warehouses ──────────────────────────────────────────────────────────

class TestSyncWarehouses:
    def test_rejects_a_warehouse_whose_branch_is_unknown(self) -> None:
        from app.api.routes.branch_warehouse import sync_warehouses

        db = FakeSession()
        result = sync_warehouses(
            WarehouseMasterSyncRequest(warehouses=[
                WarehouseMasterSyncItem(whs_code="WH-9", whs_name="Orphan", bpl_id=404),
            ]),
            db=db, current_user=_user(),
        )
        assert (result.created, result.skipped) == (0, 1)
        assert "branch BPLId 404 not in Branch Master" in result.errors[0]
        assert not [o for o in db.added if type(o).__name__ == "WarehouseMaster"]

    def test_creates_against_an_existing_branch(self) -> None:
        from app.api.routes.branch_warehouse import sync_warehouses

        branch = _branch_row(bpl_id=1)
        db = FakeSession(branches=[branch])
        result = sync_warehouses(
            WarehouseMasterSyncRequest(warehouses=[
                WarehouseMasterSyncItem(
                    whs_code="wh-01", whs_name="Mumbai Main", bpl_id=1, inactive="N",
                ),
            ]),
            db=db, current_user=_user(),
        )
        assert result.created == 1
        warehouse = next(o for o in db.added if type(o).__name__ == "WarehouseMaster")
        assert warehouse.whs_code == "WH-01"          # normalised before the key lookup
        assert warehouse.branch_id == branch.id

    def test_one_bad_row_does_not_block_the_rest_of_the_batch(self) -> None:
        from app.api.routes.branch_warehouse import sync_warehouses

        db = FakeSession(branches=[_branch_row(bpl_id=1)])
        result = sync_warehouses(
            WarehouseMasterSyncRequest(warehouses=[
                WarehouseMasterSyncItem(whs_code="WH-1", whs_name="Good", bpl_id=1),
                WarehouseMasterSyncItem(whs_code="WH-2", whs_name="Orphan", bpl_id=404),
                WarehouseMasterSyncItem(whs_code="WH-3", whs_name="Good too", bpl_id=1),
            ]),
            db=db, current_user=_user(),
        )
        assert (result.created, result.skipped) == (2, 1)
        assert len(result.errors) == 1

    def test_sap_can_reparent_a_warehouse(self) -> None:
        """Re-parenting is a SAP change — sync must follow it, unlike PUT which rejects it."""
        from app.api.routes.branch_warehouse import sync_warehouses

        old, new = _branch_row(bpl_id=1), _branch_row(bpl_id=2)
        existing = _warehouse_row(whs_code="WH-01", branch_id=old.id)
        db = FakeSession(branches=[old, new], warehouses=[existing])
        sync_warehouses(
            WarehouseMasterSyncRequest(warehouses=[
                WarehouseMasterSyncItem(whs_code="WH-01", whs_name="Mumbai Main", bpl_id=2),
            ]),
            db=db, current_user=_user(),
        )
        assert existing.branch_id == new.id

    def test_sync_never_overwrites_ops_owned_fields(self) -> None:
        from app.api.routes.branch_warehouse import sync_warehouses

        existing = _warehouse_row(is_active=False, notes="Dock under repair")
        db = FakeSession(branches=[_branch_row(bpl_id=1)], warehouses=[existing])
        sync_warehouses(
            WarehouseMasterSyncRequest(warehouses=[
                WarehouseMasterSyncItem(
                    whs_code="WH-01", whs_name="Mumbai Main", bpl_id=1,
                    is_active=True, notes="SAP clobber attempt",
                ),
            ]),
            db=db, current_user=_user(),
        )
        assert existing.is_active is False
        assert existing.notes == "Dock under repair"


# ── PUT: what ops may and may not change ──────────────────────────────────────

class TestUpdateGuards:
    def _request(self) -> SimpleNamespace:
        return SimpleNamespace(client=SimpleNamespace(host="10.0.0.1"))

    def test_branch_put_writes_ops_fields(self) -> None:
        from app.api.routes.branch_warehouse import update_branch

        branch = _branch_row()
        db = FakeSession(branches=[branch])
        # warehouse_count is recomputed from a COUNT(); the fake answers WarehouseMaster
        # lookups with scalar_one_or_none, so patch just that call.
        db.execute = lambda stmt: SimpleNamespace(scalar_one=lambda: 0)  # type: ignore[assignment]

        result = update_branch(
            branch.id, BranchMasterUpdate(is_active=False, notes="Closed for audit"),
            self._request(), db=db, current_user=_user(),
        )
        assert result.is_active is False
        assert result.notes == "Closed for audit"

    def test_branch_put_rejects_a_changed_sap_field(self) -> None:
        from app.api.routes.branch_warehouse import update_branch

        branch = _branch_row(bpl_name="Mumbai")
        db = FakeSession(branches=[branch])
        with pytest.raises(HTTPException) as exc:
            update_branch(
                branch.id, BranchMasterUpdate(bpl_name="Renamed locally", is_active=False),
                self._request(), db=db, current_user=_user(),
            )
        assert exc.value.status_code == 409
        assert branch.bpl_name == "Mumbai"

    def test_branch_put_accepts_an_unchanged_sap_field(self) -> None:
        """Round-tripping the whole object back must not be mistaken for an edit."""
        from app.api.routes.branch_warehouse import update_branch

        branch = _branch_row(bpl_name="Mumbai", city="Mumbai")
        db = FakeSession(branches=[branch])
        db.execute = lambda stmt: SimpleNamespace(scalar_one=lambda: 0)  # type: ignore[assignment]

        result = update_branch(
            branch.id,
            BranchMasterUpdate(bpl_id=branch.bpl_id, bpl_name="Mumbai", city="Mumbai", notes="ok"),
            self._request(), db=db, current_user=_user(),
        )
        assert result.notes == "ok"

    def test_branch_put_with_nothing_writable_is_rejected(self) -> None:
        from app.api.routes.branch_warehouse import update_branch

        branch = _branch_row()
        db = FakeSession(branches=[branch])
        with pytest.raises(HTTPException) as exc:
            update_branch(
                branch.id, BranchMasterUpdate(bpl_id=branch.bpl_id),
                self._request(), db=db, current_user=_user(),
            )
        assert exc.value.status_code == 422

    def test_warehouse_put_rejects_reparenting(self) -> None:
        """B1 rejects a document whose warehouse and branch disagree — SAP owns the link."""
        from app.api.routes.branch_warehouse import update_warehouse

        branch = _branch_row(bpl_id=1)
        warehouse = _warehouse_row(branch_id=branch.id)
        db = FakeSession(branches=[branch], warehouses=[warehouse])
        with pytest.raises(HTTPException) as exc:
            update_warehouse(
                warehouse.id, WarehouseMasterUpdate(bpl_id=2, is_active=False),
                self._request(), db=db, current_user=_user(),
            )
        assert exc.value.status_code == 409
        assert warehouse.branch_id == branch.id

    def test_warehouse_put_writes_ops_fields(self) -> None:
        from app.api.routes.branch_warehouse import update_warehouse

        branch = _branch_row(bpl_id=1)
        warehouse = _warehouse_row(branch_id=branch.id)
        db = FakeSession(branches=[branch], warehouses=[warehouse])

        result = update_warehouse(
            warehouse.id, WarehouseMasterUpdate(is_active=False, notes="Dock under repair"),
            self._request(), db=db, current_user=_user(),
        )
        assert result.is_active is False
        assert result.bpl_id == 1
        assert result.branch_name == branch.bpl_name

    def test_missing_records_404(self) -> None:
        from app.api.routes.branch_warehouse import update_branch, update_warehouse

        db = FakeSession()
        for fn, body in (
            (update_branch, BranchMasterUpdate(notes="x")),
            (update_warehouse, WarehouseMasterUpdate(notes="x")),
        ):
            with pytest.raises(HTTPException) as exc:
                fn(uuid.uuid4(), body, self._request(), db=db, current_user=_user())
            assert exc.value.status_code == 404
