"""
app/routers/slots.py  –  Interview slot management
"""
from typing import List, Optional
from uuid import UUID
from datetime import datetime, timezone, date as dt_date, time as dt_time

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.models.models import InterviewSlot, Booking, BookingStatus, SlotStatus
from app.schemas.schemas import SlotCreate, SlotUpdate, SlotOut, SlotBookingSummary
from app.services.auth import get_current_hr
from app.services.slot_capacity import (
    future_slots_filter,
    open_only_slots_filter,
    repair_slots_state,
)
from app.services.slot_restriction import filter_slots_for_position, load_restriction_index

router = APIRouter(prefix="/api/slots", tags=["Slots"])


def _parse_time(t_str: str):
    from datetime import time as dt_time
    parts = t_str.split(":")
    return dt_time(int(parts[0]), int(parts[1]))


@router.get("", response_model=List[SlotOut])
async def list_slots(
    open_only: bool = False,
    position_id: Optional[UUID] = Query(None, description="Filter by position restriction rules"),
    db: AsyncSession = Depends(get_db),
):
    """
    Public endpoint for fetching interview slots.

    Parameters:
    - open_only (bool): If true, only return open slots with remaining capacity
    - position_id: When set with open_only, hide slots blocked for this position
      (position-exclusive or group-exclusive per HR rules)

    Always filters out past slots and returns only future slots.
    Uses Taiwan timezone (UTC+8) for time comparisons.
    """
    from app.services.timezone_utils import get_taipei_date_and_time
    
    # Get current date and time in Taiwan timezone
    current_date, current_time = get_taipei_date_and_time()
    
    # Base query
    q = select(InterviewSlot).order_by(InterviewSlot.slot_date, InterviewSlot.start_time)

    if open_only:
        await repair_slots_state(db)
        q = q.where(open_only_slots_filter())

    # Hide once start time has passed (e.g. 11:30–12:00 hidden starting at 11:30)
    q = q.where(future_slots_filter(current_date, current_time))

    result = await db.execute(q)
    slots = result.scalars().all()

    if open_only and position_id and slots:
        index = await load_restriction_index(db)
        allowed_ids = await filter_slots_for_position(
            db,
            [s.id for s in slots],
            position_id,
            index=index,
        )
        slots = [s for s in slots if s.id in allowed_ids]

    return slots


@router.get("/bookings-by-date", tags=["Slots"])
async def get_bookings_by_date(
    db: AsyncSession = Depends(get_db),
):
    """
    【面試管理日曆專用】
    直接從 Booking 表撈出所有有效預約，以 booking.slot_date 為日期維度回傳。
    這樣即使 HR 在「所有預約紀錄」將面試時間改成不存在於 InterviewSlot 的自訂時間，
    日曆仍能正確顯示更新後的日期與名稱。

    回傳格式：
    {
      "2026-06-15": [
        { "time": "10:00-11:00", "applicant_name": "王小明", "position_title": "工程師" },
        ...
      ],
      ...
    }
    """
    from sqlalchemy.orm import joinedload

    result = await db.execute(
        select(Booking)
        .options(joinedload(Booking.position))
        .where(
            Booking.status.in_([
                BookingStatus.completed,
                BookingStatus.auto_completed,
                BookingStatus.no_show,
            ]),
            Booking.slot_date.isnot(None),
            Booking.start_time.isnot(None),
        )
    )
    bookings = result.scalars().all()

    by_date: dict = {}
    for b in bookings:
        date_str = str(b.slot_date)
        start_str = b.start_time.strftime("%H:%M") if b.start_time else ""
        end_str   = b.end_time.strftime("%H:%M")   if b.end_time   else ""
        if date_str not in by_date:
            by_date[date_str] = []
        by_date[date_str].append({
            "time": f"{start_str}-{end_str}",
            "applicant_name": b.applicant_name or "",
            "position_title": b.position.title if b.position else "無職缺",
            "status": b.status.value if hasattr(b.status, "value") else str(b.status),
        })

    # 每個日期內按時間排序
    for date_str in by_date:
        by_date[date_str].sort(key=lambda x: x["time"])

    return by_date


@router.get("/{slot_id}/bookings", response_model=List[SlotBookingSummary])
async def get_slot_bookings(
    slot_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    """
    Return active bookings for a slot (for calendar preview panel).
    Includes: completed, auto_completed, no_show
    Excludes: canceled (已取消預約)

    This endpoint is called frequently to refresh the preview panel with latest booking statuses.
    """
    from sqlalchemy.orm import joinedload

    q = (
        select(Booking)
        .options(joinedload(Booking.position))
        .where(
            Booking.slot_id == slot_id,
            Booking.status.in_([
                BookingStatus.completed,
                BookingStatus.auto_completed,
                BookingStatus.no_show  # Include no_show so calendar can show status changes
            ]),
        )
    )
    result = await db.execute(q)
    bookings = result.scalars().all()

    return [
        SlotBookingSummary(
            applicant_name=b.applicant_name,
            position_title=b.position.title if b.position else None,
        )
        for b in bookings
    ]


@router.post("", response_model=SlotOut)
async def create_slot(
    body: SlotCreate,
    db: AsyncSession = Depends(get_db),
    _hr: str = Depends(get_current_hr),
):
    """
    Create a new interview slot.

    Validation:
    - Slot cannot be in the past.
    - Slot must be from now onwards.
    """
    # Get current date and time
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    current_date = now.date()
    current_time = now.time()

    # Validate slot is not in the past
    slot_datetime = datetime.combine(body.slot_date, _parse_time(body.start_time))
    current_datetime = datetime.combine(current_date, current_time)

    if slot_datetime < current_datetime:
        raise HTTPException(
            status_code=400,
            detail="新增面試時段不能在過去的時間。請選擇現在或現在以後的時間。"
        )

    slot = InterviewSlot(
        slot_date=body.slot_date,
        start_time=_parse_time(body.start_time),
        end_time=_parse_time(body.end_time),
        max_capacity=body.max_capacity,
        notes=body.notes,
    )
    db.add(slot)
    await db.commit()
    await db.refresh(slot)
    return slot


@router.patch("/{slot_id}", response_model=SlotOut)
async def update_slot(
    slot_id: UUID,
    body: SlotUpdate,
    db: AsyncSession = Depends(get_db),
    _hr: str = Depends(get_current_hr),
):
    """
    Update a slot.

    Validation:
    - Updated slot cannot be in the past.
    """
    result = await db.execute(select(InterviewSlot).where(InterviewSlot.id == slot_id))
    slot = result.scalar_one_or_none()
    if not slot:
        raise HTTPException(404, "Slot not found")

    # Get current date and time
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    current_date = now.date()
    current_time = now.time()

    # Determine the new date and time for validation
    new_date = body.slot_date if body.slot_date is not None else slot.slot_date
    new_start_time = _parse_time(body.start_time) if body.start_time is not None else slot.start_time

    # Validate updated slot is not in the past
    slot_datetime = datetime.combine(new_date, new_start_time)
    current_datetime = datetime.combine(current_date, current_time)

    if slot_datetime < current_datetime:
        raise HTTPException(
            status_code=400,
            detail="面試時段不能在過去的時間。請選擇現在或現在以後的時間。"
        )

    if body.slot_date is not None:
        slot.slot_date = body.slot_date
    if body.start_time is not None:
        slot.start_time = _parse_time(body.start_time)
    if body.end_time is not None:
        slot.end_time = _parse_time(body.end_time)
    if body.max_capacity is not None:
        slot.max_capacity = body.max_capacity
    if body.notes is not None:
        slot.notes = body.notes
    if body.status is not None:
        slot.status = body.status

    await db.commit()
    await db.refresh(slot)
    return slot


@router.delete("/{slot_id}")
async def delete_slot(
    slot_id: UUID,
    db: AsyncSession = Depends(get_db),
    _hr: str = Depends(get_current_hr),
):
    result = await db.execute(select(InterviewSlot).where(InterviewSlot.id == slot_id))
    slot = result.scalar_one_or_none()
    if not slot:
        raise HTTPException(404, "Slot not found")

    await db.delete(slot)
    await db.commit()
    return {"message": "Slot deleted"}