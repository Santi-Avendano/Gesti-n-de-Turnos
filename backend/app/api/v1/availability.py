from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentPrincipal, require_admin
from app.db.session import get_db
from app.schemas.availability import (
    ExceptionIn,
    ExceptionListResponse,
    ExceptionOut,
    ExceptionPatch,
    GridReplaceRequest,
    GridResponse,
    GridRuleOut,
)
from app.services import availability_service

router = APIRouter()


# ---------- grid ----------

@router.get("/grid")
async def get_grid(
    principal: CurrentPrincipal = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> GridResponse:
    rows = await availability_service.list_grid(db, organization_id=principal.org_id)
    return GridResponse(
        rules=[
            GridRuleOut(
                id=r.id,
                day_of_week=r.day_of_week,
                start_local_time=r.start_local_time,
                end_local_time=r.end_local_time,
            )
            for r in rows
        ]
    )


@router.put("/grid")
async def put_grid(
    body: GridReplaceRequest,
    principal: CurrentPrincipal = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> GridResponse:
    rows = await availability_service.replace_grid(
        db,
        organization_id=principal.org_id,
        rules=[(r.day_of_week, r.start_local_time, r.end_local_time) for r in body.rules],
    )
    return GridResponse(
        rules=[
            GridRuleOut(
                id=r.id,
                day_of_week=r.day_of_week,
                start_local_time=r.start_local_time,
                end_local_time=r.end_local_time,
            )
            for r in rows
        ]
    )


# ---------- exceptions ----------

def _to_exc(row) -> ExceptionOut:
    return ExceptionOut(
        id=row.id,
        start_at_utc=row.start_at_utc,
        end_at_utc=row.end_at_utc,
        kind=row.kind,
        reason=row.reason,
    )


@router.get("/exceptions")
async def list_exceptions(
    from_at: datetime | None = Query(default=None, alias="from"),
    to_at: datetime | None = Query(default=None, alias="to"),
    principal: CurrentPrincipal = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> ExceptionListResponse:
    rows = await availability_service.list_exceptions(
        db,
        organization_id=principal.org_id,
        from_at=from_at,
        to_at=to_at,
    )
    return ExceptionListResponse(items=[_to_exc(r) for r in rows])


@router.get("/exceptions/{exception_id}")
async def get_exception(
    exception_id: int,
    principal: CurrentPrincipal = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> ExceptionOut:
    row = await availability_service.get_exception(
        db, organization_id=principal.org_id, exception_id=exception_id
    )
    return _to_exc(row)


@router.post("/exceptions", status_code=status.HTTP_201_CREATED)
async def create_exception(
    body: ExceptionIn,
    principal: CurrentPrincipal = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> ExceptionOut:
    row = await availability_service.create_exception(
        db,
        organization_id=principal.org_id,
        start_at_utc=body.start_at_utc,
        end_at_utc=body.end_at_utc,
        kind=body.kind,
        reason=body.reason,
    )
    return _to_exc(row)


@router.patch("/exceptions/{exception_id}")
async def patch_exception(
    exception_id: int,
    body: ExceptionPatch,
    principal: CurrentPrincipal = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> ExceptionOut:
    row = await availability_service.update_exception(
        db,
        organization_id=principal.org_id,
        exception_id=exception_id,
        start_at_utc=body.start_at_utc,
        end_at_utc=body.end_at_utc,
        kind=body.kind,
        reason=body.reason,
    )
    return _to_exc(row)


@router.delete("/exceptions/{exception_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_exception(
    exception_id: int,
    principal: CurrentPrincipal = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> None:
    await availability_service.delete_exception(
        db, organization_id=principal.org_id, exception_id=exception_id
    )
