"""
Branch Master (SAP OBPL) and Warehouse Master (SAP OWHS) routes — Phase 8.

Same REST convention as the rest of master data (rationale in the app/schemas/api.py
Master Data header and in app/api/routes/master_data.py):

  POST .../sync   — bulk upsert pushed FROM SAP.
  GET  ...        — reads local tables only, never SAP live.
  PUT  .../{id}   — ops-side correction of a single record.

These two differ from ship-to / bill-to in one important way. Those describe the
*retailer's* locations and carry an ops mapping decision (which B1 warehouse, which BP
address). Branch and warehouse describe **our own** SAP org structure, so SAP authors
every business field and there is nothing to map: `is_active` and `notes` are the only
locally writable columns, and a re-sync can never undo them.

They live in their own module rather than in master_data.py only to keep that file at a
readable size; the router prefix and OpenAPI tag are identical, so they appear alongside
the other master-data endpoints in /docs.

Branches:   GET /api/master-data/branches,   PUT .../{id}, POST .../sync
Warehouses: GET /api/master-data/warehouses, PUT .../{id}, POST .../sync
"""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from app.api.deps import get_sync_db
from app.api.routes.auth import get_current_user
from app.api.routes.master_data import _reject_immutable_changes
from app.schemas.api import (
    BranchMasterResponse,
    BranchMasterSyncItem,
    BranchMasterSyncRequest,
    BranchMasterUpdate,
    MasterDataSyncResult,
    PaginatedResponse,
    UserResponse,
    WarehouseMasterResponse,
    WarehouseMasterSyncItem,
    WarehouseMasterSyncRequest,
    WarehouseMasterUpdate,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.models.master_data import BranchMaster, WarehouseMaster

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/master-data", tags=["Master Data"])

# Locally owned; never written by sync even when a round-tripped payload carries them.
_OPS_OWNED = ("is_active", "notes")

# Derived or server-assigned. Accepted on PUT so a GET response can be posted straight
# back, then ignored — unlike the sync-owned business fields below, there is no user
# edit to preserve or reject, so a mismatch is not worth a 409.
_BRANCH_DERIVED = {"id", "warehouse_count", "created_at", "updated_at"}
_WAREHOUSE_DERIVED = {"id", "branch_name", "created_at", "updated_at"}

# Owned by SAP via .../sync. Rejected on PUT if changed, never silently dropped.
_BRANCH_SAP_OWNED = {
    "bpl_name": "bpl_name",
    "disabled": "disabled",
    "address": "address",
    "street": "street",
    "block": "block",
    "city": "city",
    "zip_code": "zip_code",
    "state": "state",
    "country": "country",
    "gstin": "gstin",
}
_WAREHOUSE_SAP_OWNED = {
    "whs_name": "whs_name",
    "inactive": "inactive",
    "location": "location",
    "street": "street",
    "block": "block",
    "city": "city",
    "zip_code": "zip_code",
    "state": "state",
    "country": "country",
}

_SYNC_REASON = (
    "This field comes from SAP via POST /api/master-data/{path}/sync. "
    "Change it in SAP and re-sync — editing it here would be overwritten on the next push."
)


# ── Branch Master (SAP OBPL) ──────────────────────────────────────────────────

def _branch_to_response(b: BranchMaster, warehouse_count: int) -> BranchMasterResponse:
    return BranchMasterResponse(
        id=b.id,
        bpl_id=b.bpl_id,
        bpl_name=b.bpl_name,
        disabled=b.disabled,
        address=b.address,
        street=b.street,
        block=b.block,
        city=b.city,
        zip_code=b.zip_code,
        state=b.state,
        country=b.country,
        gstin=b.gstin,
        is_active=b.is_active,
        notes=b.notes,
        warehouse_count=warehouse_count,
        created_at=b.created_at,
        updated_at=b.updated_at,
    )


@router.get("/branches", response_model=PaginatedResponse[BranchMasterResponse])
def list_branches(
    is_active: bool | None = Query(None),
    disabled: bool | None = Query(None, description="SAP's own OBPL.Disabled flag"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_sync_db),
    _current_user: UserResponse = Depends(get_current_user),
) -> PaginatedResponse[BranchMasterResponse]:
    from sqlalchemy import func, select

    from app.models.master_data import BranchMaster, WarehouseMaster

    whs_count = (
        select(WarehouseMaster.branch_id, func.count().label("n"))
        .where(WarehouseMaster.deleted_at.is_(None))
        .group_by(WarehouseMaster.branch_id)
        .subquery()
    )

    q = (
        select(BranchMaster, func.coalesce(whs_count.c.n, 0).label("warehouse_count"))
        .outerjoin(whs_count, whs_count.c.branch_id == BranchMaster.id)
        .where(BranchMaster.deleted_at.is_(None))
        .order_by(BranchMaster.bpl_id)
    )
    if is_active is not None:
        q = q.where(BranchMaster.is_active == is_active)
    if disabled is not None:
        q = q.where(BranchMaster.disabled == disabled)

    total = db.execute(select(func.count()).select_from(q.subquery())).scalar_one()
    rows = db.execute(q.limit(limit).offset(offset)).all()

    items = [_branch_to_response(row.BranchMaster, row.warehouse_count) for row in rows]
    return PaginatedResponse(items=items, total=total, limit=limit, offset=offset)


@router.put("/branches/{branch_id}", response_model=BranchMasterResponse)
def update_branch(
    branch_id: uuid.UUID,
    body: BranchMasterUpdate,
    request: Request,
    db: Session = Depends(get_sync_db),
    current_user: UserResponse = Depends(get_current_user),
) -> BranchMasterResponse:
    """
    Ops-side edit of one branch. Only `is_active` and `notes` are writable — SAP owns
    the rest. Parking a branch here stops this middleware using it without waiting for
    a SAP change; it does not alter anything in B1.
    """
    from sqlalchemy import func, select

    from app.models.audit_log import AuditLog
    from app.models.master_data import BranchMaster, WarehouseMaster

    branch = db.get(BranchMaster, branch_id)
    if not branch or branch.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Branch not found")

    _reject_immutable_changes(
        body, branch, {"bpl_id": "bpl_id"},
        reason="bpl_id is the SAP branch key and cannot be changed here.",
    )
    _reject_immutable_changes(
        body, branch, _BRANCH_SAP_OWNED,
        reason=_SYNC_REASON.format(path="branches"),
    )

    if all(getattr(body, f) is None for f in _OPS_OWNED):
        raise HTTPException(status_code=422, detail="No fields to update")

    for f in _OPS_OWNED:
        v = getattr(body, f)
        if v is not None:
            setattr(branch, f, v)

    db.add(AuditLog(
        user_email=current_user.email,
        action="update_branch",
        entity_type="BranchMaster",
        entity_id=str(branch_id),
        payload=body.model_dump(include=set(_OPS_OWNED), exclude_none=True, mode="json"),
        ip_address=request.client.host if request.client else None,
    ))
    db.flush()
    db.commit()

    count = db.execute(
        select(func.count()).select_from(WarehouseMaster).where(
            WarehouseMaster.branch_id == branch.id,
            WarehouseMaster.deleted_at.is_(None),
        )
    ).scalar_one()
    return _branch_to_response(branch, count)


@router.post("/branches", response_model=BranchMasterResponse)
def upsert_branch(
    body: BranchMasterSyncItem,
    response: Response,
    db: Session = Depends(get_sync_db),
    current_user: UserResponse = Depends(get_current_user),
) -> BranchMasterResponse:
    """
    Add or update one branch, keyed on `bpl_id` (= SAP `OBPL.BPLId`).

    The single-record twin of `/branches/sync`, for the same reason the other master
    data has one: SAP does not track what we already hold, and making it ask first —
    POST, read a conflict, switch to PUT — is two round trips and a race.

    **201 on create, 200 on update.** The body is the same object `/branches/sync`
    takes in its list, so the two cannot drift apart.

    `disabled` is SAP's flag and is always applied — a branch SAP has just re-enabled
    must stop being treated as closed. `is_active` and `notes` are ours and are never
    touched, so a push cannot undo an ops decision to park a branch.
    """
    from sqlalchemy import select

    from app.models.audit_log import AuditLog
    from app.models.master_data import BranchMaster

    branch = db.execute(
        select(BranchMaster).where(BranchMaster.bpl_id == body.bpl_id)
    ).scalar_one_or_none()

    if branch is not None and branch.deleted_at is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Branch BPLId {body.bpl_id} is soft-deleted here. Restore it before "
                f"syncing, so an automated push cannot undo a deliberate removal."
            ),
        )

    fields = body.model_dump(exclude={"bpl_id"} | _BRANCH_DERIVED | set(_OPS_OWNED))
    created = branch is None
    if created:
        branch = BranchMaster(bpl_id=body.bpl_id, **fields)
        db.add(branch)
    else:
        for key, value in fields.items():
            setattr(branch, key, value)

    db.add(AuditLog(
        user_email=current_user.email,
        action="create_branch" if created else "update_branch",
        entity_type="BranchMaster",
        payload=body.model_dump(mode="json"),
    ))
    db.commit()
    db.refresh(branch)
    response.status_code = 201 if created else 200
    return _branch_to_response(branch, _warehouse_count(db, branch.id))


def _warehouse_count(db: Session, branch_id: uuid.UUID) -> int:
    """Live warehouses under a branch — the one field the response carries that the
    branch row does not."""
    from sqlalchemy import func, select

    from app.models.master_data import WarehouseMaster

    return db.execute(
        select(func.count()).select_from(WarehouseMaster).where(
            WarehouseMaster.branch_id == branch_id,
            WarehouseMaster.deleted_at.is_(None),
        )
    ).scalar_one()


@router.post("/branches/sync", response_model=MasterDataSyncResult)
def sync_branches(
    body: BranchMasterSyncRequest,
    db: Session = Depends(get_sync_db),
    current_user: UserResponse = Depends(get_current_user),
) -> MasterDataSyncResult:
    """
    Bulk upsert Branch Master (OBPL) records pushed from SAP, keyed by `bpl_id`.

    Safe to create here: a branch carries no integration config, only org data.
    `disabled` is SAP's authoritative flag and is always overwritten — a branch SAP has
    just re-enabled must stop being treated as closed on the next push. `is_active` and
    `notes` are ours and are never touched, so a re-sync cannot undo an ops decision.
    """
    from sqlalchemy import select

    from app.models.audit_log import AuditLog
    from app.models.master_data import BranchMaster

    created = updated = skipped = 0
    errors: list[str] = []

    for item in body.branches:
        branch = db.execute(
            select(BranchMaster).where(BranchMaster.bpl_id == item.bpl_id)
        ).scalar_one_or_none()

        fields = item.model_dump(exclude={"bpl_id"} | _BRANCH_DERIVED | set(_OPS_OWNED))

        if branch:
            if branch.deleted_at is not None:
                skipped += 1
                errors.append(f"BPLId {item.bpl_id}: soft-deleted — restore manually before syncing")
                continue
            for k, v in fields.items():
                setattr(branch, k, v)
            updated += 1
        else:
            db.add(BranchMaster(bpl_id=item.bpl_id, **fields))
            created += 1

    db.add(AuditLog(
        user_email=current_user.email,
        action="sync_branches",
        entity_type="BranchMaster",
        payload={"created": created, "updated": updated, "skipped": skipped},
    ))
    db.commit()
    return MasterDataSyncResult(created=created, updated=updated, skipped=skipped, errors=errors)


# ── Warehouse Master (SAP OWHS) ───────────────────────────────────────────────

def _warehouse_to_response(
    w: WarehouseMaster, bpl_id: int, branch_name: str | None,
) -> WarehouseMasterResponse:
    return WarehouseMasterResponse(
        id=w.id,
        whs_code=w.whs_code,
        whs_name=w.whs_name,
        bpl_id=bpl_id,
        branch_name=branch_name,
        inactive=w.inactive,
        location=w.location,
        street=w.street,
        block=w.block,
        city=w.city,
        zip_code=w.zip_code,
        state=w.state,
        country=w.country,
        is_active=w.is_active,
        notes=w.notes,
        created_at=w.created_at,
        updated_at=w.updated_at,
    )


@router.get("/warehouses", response_model=PaginatedResponse[WarehouseMasterResponse])
def list_warehouses(
    bpl_id: int | None = Query(None, description="Filter to one branch"),
    is_active: bool | None = Query(None),
    inactive: bool | None = Query(None, description="SAP's own OWHS.Inactive flag"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_sync_db),
    _current_user: UserResponse = Depends(get_current_user),
) -> PaginatedResponse[WarehouseMasterResponse]:
    from sqlalchemy import func, select

    from app.models.master_data import BranchMaster, WarehouseMaster

    q = (
        select(WarehouseMaster, BranchMaster.bpl_id, BranchMaster.bpl_name)
        .join(BranchMaster, WarehouseMaster.branch_id == BranchMaster.id)
        .where(WarehouseMaster.deleted_at.is_(None))
        .order_by(BranchMaster.bpl_id, WarehouseMaster.whs_code)
    )
    if bpl_id is not None:
        q = q.where(BranchMaster.bpl_id == bpl_id)
    if is_active is not None:
        q = q.where(WarehouseMaster.is_active == is_active)
    if inactive is not None:
        q = q.where(WarehouseMaster.inactive == inactive)

    total = db.execute(select(func.count()).select_from(q.subquery())).scalar_one()
    rows = db.execute(q.limit(limit).offset(offset)).all()

    items = [
        _warehouse_to_response(row.WarehouseMaster, row.bpl_id, row.bpl_name) for row in rows
    ]
    return PaginatedResponse(items=items, total=total, limit=limit, offset=offset)


@router.put("/warehouses/{warehouse_id}", response_model=WarehouseMasterResponse)
def update_warehouse(
    warehouse_id: uuid.UUID,
    body: WarehouseMasterUpdate,
    request: Request,
    db: Session = Depends(get_sync_db),
    current_user: UserResponse = Depends(get_current_user),
) -> WarehouseMasterResponse:
    """
    Ops-side edit of one warehouse. Only `is_active` and `notes` are writable.

    The branch link is deliberately not editable here: B1 rejects a document whose
    warehouse and branch disagree, so re-parenting is a SAP change followed by a sync.
    """
    from app.models.audit_log import AuditLog
    from app.models.master_data import BranchMaster, WarehouseMaster

    warehouse = db.get(WarehouseMaster, warehouse_id)
    if not warehouse or warehouse.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Warehouse not found")

    branch = db.get(BranchMaster, warehouse.branch_id)

    _reject_immutable_changes(
        body, warehouse, {"whs_code": "whs_code"},
        reason="whs_code is the SAP warehouse key and cannot be changed here.",
    )
    if branch is not None:
        _reject_immutable_changes(
            body, branch, {"bpl_id": "bpl_id"},
            reason=(
                "The branch link comes from SAP via POST /api/master-data/warehouses/sync. "
                "B1 rejects a document whose warehouse and branch disagree, so re-parent "
                "the warehouse in SAP and re-sync."
            ),
        )
    _reject_immutable_changes(
        body, warehouse, _WAREHOUSE_SAP_OWNED,
        reason=_SYNC_REASON.format(path="warehouses"),
    )

    if all(getattr(body, f) is None for f in _OPS_OWNED):
        raise HTTPException(status_code=422, detail="No fields to update")

    for f in _OPS_OWNED:
        v = getattr(body, f)
        if v is not None:
            setattr(warehouse, f, v)

    db.add(AuditLog(
        user_email=current_user.email,
        action="update_warehouse",
        entity_type="WarehouseMaster",
        entity_id=str(warehouse_id),
        payload=body.model_dump(include=set(_OPS_OWNED), exclude_none=True, mode="json"),
        ip_address=request.client.host if request.client else None,
    ))
    db.flush()
    db.commit()

    return _warehouse_to_response(
        warehouse,
        branch.bpl_id if branch else 0,
        branch.bpl_name if branch else None,
    )


@router.post("/warehouses", response_model=WarehouseMasterResponse)
def upsert_warehouse(
    body: WarehouseMasterSyncItem,
    response: Response,
    db: Session = Depends(get_sync_db),
    current_user: UserResponse = Depends(get_current_user),
) -> WarehouseMasterResponse:
    """
    Add or update one warehouse, keyed on `whs_code` (= SAP `OWHS.WhsCode`).

    The single-record twin of `/warehouses/sync`, taking the same object that endpoint
    takes in its list. **201 on create, 200 on update.**

    `bpl_id` must already name a branch we hold — **push the branch first**. A warehouse
    pointing at a branch that does not exist is refused rather than stored with a
    dangling link, the same rule that makes a SKU mapping depend on Item Master: a
    warehouse whose branch is unknown cannot decide place of supply, and the failure
    would surface much later as a rejected Sales Order.

    `is_active` and `notes` are ours and are never touched, so a push cannot undo an
    ops decision to park a warehouse.
    """
    from sqlalchemy import select

    from app.models.audit_log import AuditLog
    from app.models.master_data import BranchMaster, WarehouseMaster

    code = body.whs_code.strip().upper()

    branch = db.execute(
        select(BranchMaster).where(
            BranchMaster.bpl_id == body.bpl_id,
            BranchMaster.deleted_at.is_(None),
        )
    ).scalar_one_or_none()
    if branch is None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Branch BPLId {body.bpl_id} is not in Branch Master, so warehouse "
                f"{code} has nothing to belong to. Push the branch first — "
                f"POST /api/master-data/branches."
            ),
        )

    warehouse = db.execute(
        select(WarehouseMaster).where(WarehouseMaster.whs_code == code)
    ).scalar_one_or_none()

    if warehouse is not None and warehouse.deleted_at is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Warehouse {code} is soft-deleted here. Restore it before syncing, so "
                f"an automated push cannot undo a deliberate removal."
            ),
        )

    fields = body.model_dump(
        exclude={"whs_code", "bpl_id"} | _WAREHOUSE_DERIVED | set(_OPS_OWNED)
    )
    created = warehouse is None
    if created:
        warehouse = WarehouseMaster(whs_code=code, branch_id=branch.id, **fields)
        db.add(warehouse)
    else:
        warehouse.branch_id = branch.id
        for key, value in fields.items():
            setattr(warehouse, key, value)

    db.add(AuditLog(
        user_email=current_user.email,
        action="create_warehouse" if created else "update_warehouse",
        entity_type="WarehouseMaster",
        payload=body.model_dump(mode="json"),
    ))
    db.commit()
    db.refresh(warehouse)
    response.status_code = 201 if created else 200
    return _warehouse_to_response(warehouse, branch.bpl_id, branch.bpl_name)


@router.post("/warehouses/sync", response_model=MasterDataSyncResult)
def sync_warehouses(
    body: WarehouseMasterSyncRequest,
    db: Session = Depends(get_sync_db),
    current_user: UserResponse = Depends(get_current_user),
) -> MasterDataSyncResult:
    """
    Bulk upsert Warehouse Master (OWHS) records pushed from SAP, keyed by `whs_code`.

    `bpl_id` must already exist in branch_master — **push branches before warehouses**.
    A row naming an unknown branch is skipped and reported rather than created with a
    dangling link, the same rule that makes SKU mapping depend on Item Master. Two
    branches are looked up once per row; batches are small enough that this is cheap.
    """
    from sqlalchemy import select

    from app.models.audit_log import AuditLog
    from app.models.master_data import BranchMaster, WarehouseMaster

    created = updated = skipped = 0
    errors: list[str] = []
    branch_ids: dict[int, uuid.UUID] = {}

    for item in body.warehouses:
        code = item.whs_code.strip().upper()

        if item.bpl_id not in branch_ids:
            branch = db.execute(
                select(BranchMaster).where(
                    BranchMaster.bpl_id == item.bpl_id,
                    BranchMaster.deleted_at.is_(None),
                )
            ).scalar_one_or_none()
            if not branch:
                skipped += 1
                errors.append(
                    f"{code}: branch BPLId {item.bpl_id} not in Branch Master — "
                    f"push POST /api/master-data/branches/sync first"
                )
                continue
            branch_ids[item.bpl_id] = branch.id

        warehouse = db.execute(
            select(WarehouseMaster).where(WarehouseMaster.whs_code == code)
        ).scalar_one_or_none()

        fields = item.model_dump(
            exclude={"whs_code", "bpl_id"} | _WAREHOUSE_DERIVED | set(_OPS_OWNED)
        )

        if warehouse:
            if warehouse.deleted_at is not None:
                skipped += 1
                errors.append(f"{code}: soft-deleted — restore manually before syncing")
                continue
            warehouse.branch_id = branch_ids[item.bpl_id]
            for k, v in fields.items():
                setattr(warehouse, k, v)
            updated += 1
        else:
            db.add(WarehouseMaster(
                whs_code=code,
                branch_id=branch_ids[item.bpl_id],
                **fields,
            ))
            created += 1

    db.add(AuditLog(
        user_email=current_user.email,
        action="sync_warehouses",
        entity_type="WarehouseMaster",
        payload={"created": created, "updated": updated, "skipped": skipped},
    ))
    db.commit()
    return MasterDataSyncResult(created=created, updated=updated, skipped=skipped, errors=errors)
