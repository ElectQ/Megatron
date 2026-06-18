from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..core.security import admin_auth

router = APIRouter(prefix="/api/admin/schedules", tags=["schedules"])


class ScheduleOut(BaseModel):
    id: str
    name: str
    next_run: str | None
    trigger: str


@router.get("", response_model=list[ScheduleOut], dependencies=[Depends(admin_auth)])
async def list_schedules():
    from ..scheduler import list_schedules as _list

    return [ScheduleOut(**s) for s in _list()]


@router.post("/reload", dependencies=[Depends(admin_auth)])
async def reload_schedules():
    from ..scheduler import reload_module_schedules

    count = await reload_module_schedules()
    return {"scheduled_modules": count}


__all__ = ["router"]
