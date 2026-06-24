# app/routers/bookings.py
"""
app/routers/bookings.py
─────────────────────────────────────────────────────────────
All booking endpoints:
  - Applicant: create, modify, cancel, lookup by email
  - HR: list, patch, delete, export
"""
import io
import logging
from datetime import datetime, timedelta, date as dt_date, time as dt_time
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_, func, and_
from sqlalchemy.orm import joinedload

from app.database import get_db
from app.models.models import (
    Booking, BookingStatus, BookingHistory, InterviewSlot, Position, SlotStatus,
)
from app.schemas.schemas import (
    BookingCreate, BookingModify, BookingHRUpdate,
    BookingOut, BookingByEmailOut, BookingEditOptions,
    BookingHistoryOut,
    InterviewPayload,  # AppsScript 串接
    PositionOut, SlotOut,
)
from app.services.auth import get_current_hr
from app.services import google_calendar_manager, email_service
from app.services.timezone_utils import get_taipei_date_and_time, get_taipei_now_no_tz
from app.config import settings, DEFAULT_ATTENDEES, POSITION_ATTENDEE_RULES
from app.services.slot_capacity import slot_is_bookable
from app.services.slot_restriction import is_slot_available_for_position

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/bookings", tags=["Bookings"])


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────

def _parse_time(t_str: str) -> dt_time:
    parts = t_str.split(":")
    return dt_time(int(parts[0]), int(parts[1]))


def _get_internal_attendees(position_title: str) -> List[dict]:
    """
    根據職位名稱取得對應的內部參與者（面試官）
    """
    attendees = DEFAULT_ATTENDEES.copy()

    for keywords, members in POSITION_ATTENDEE_RULES.items():
        if isinstance(keywords, str):
            keywords = (keywords,)

        if all(keyword in position_title for keyword in keywords):
            for member in members:
                if member not in attendees:
                    attendees.append(member)

    return attendees


async def _auto_complete_expired_bookings(db: AsyncSession) -> int:
    """
    自動完成過期的預約：
    將所有狀態為 'completed' 且面試時間已過期的預約自動更新為 'auto_completed'
    """
    current_date, current_time = get_taipei_date_and_time()

    result = await db.execute(
        select(Booking)
        .where(Booking.status == BookingStatus.completed)
    )

    bookings = result.scalars().all()
    updated_count = 0

    for booking in bookings:
        if booking.slot_date and booking.start_time:
            is_past = (
                    current_date > booking.slot_date or
                    (current_date == booking.slot_date and current_time > booking.start_time)
            )

            if is_past:
                booking.status = BookingStatus.auto_completed
                updated_count += 1

    if updated_count > 0:
        await db.commit()
        logger.info(f"Auto-completed {updated_count} expired bookings")

    return updated_count


# ─────────────────────────────────────────────────────────────
# Async background tasks for Google Calendar operations
# ─────────────────────────────────────────────────────────────

async def _delete_calendar_event_async(event_id: str):
    """後台異步刪除 Google Calendar 事件"""
    try:
        google_calendar_manager.delete_event(
            event_id=event_id,
            send_ical=True,
        )
        logger.info(f"Calendar event {event_id} deleted successfully")
    except Exception as e:
        logger.warning(f"Failed to delete calendar event {event_id}: {e}")


async def _recreate_calendar_event_async(
        old_event_id: str,
        applicant_name: str,
        applicant_email: str,
        position_title: str,
        slot_date,
        start_time,
        end_time,
):
    """後台異步重建 Google Calendar 事件"""
    try:
        meet_link, new_event_id = google_calendar_manager.recreate_meet_link(
            event_id=old_event_id,
            applicant_name=applicant_name,
            applicant_email=applicant_email,
            position_title=position_title,
            slot_date=slot_date,
            start_time=start_time,
            end_time=end_time,
        )
        logger.info(f"Calendar event updated: {old_event_id} → {new_event_id}, New meet link: {meet_link}")
    except Exception as e:
        logger.warning(f"Failed to recreate calendar event: {e}")


async def _update_calendar_event_async(
        event_id: str,
        summary: str,
        description: str,
        attendees: List[str],
):
    """
    後台異步原地更新 Google Calendar 行程。
    使用 update_event（不刪除、不新建），行程 ID 保持不變。
    """
    try:
        meet_link, returned_id = google_calendar_manager.update_event(
            event_id=event_id,
            summary=summary,
            description=description,
            attendees=attendees,
            send_ical=True,
        )
        if returned_id:
            logger.info(
                f"Calendar event {event_id} updated in-place successfully. "
                f"meet_link={meet_link}"
            )
        else:
            logger.error(
                f"update_event returned None for event {event_id}. "
                f"The event may not have been updated."
            )
    except Exception as e:
        logger.error(f"Failed to update calendar event {event_id}: {e}", exc_info=True)


async def _get_active_booking_for_email(db: AsyncSession, email: str) -> Optional[Booking]:
    """Return the latest active (completed / auto_completed) booking for an email."""
    result = await db.execute(
        select(Booking)
        .options(joinedload(Booking.position), joinedload(Booking.slot))
        .where(
            Booking.applicant_email == email,
            Booking.status.in_([BookingStatus.completed, BookingStatus.auto_completed]),
        )
        .order_by(Booking.booked_at.desc())
    )
    return result.scalars().first()


async def _get_slot_or_404(db: AsyncSession, slot_id: UUID) -> InterviewSlot:
    result = await db.execute(select(InterviewSlot).where(InterviewSlot.id == slot_id))
    slot = result.scalar_one_or_none()
    if not slot:
        raise HTTPException(404, "Interview slot not found")
    return slot


async def _get_position_or_404(db: AsyncSession, position_id: UUID) -> Position:
    result = await db.execute(select(Position).where(Position.id == position_id))
    pos = result.scalar_one_or_none()
    if not pos:
        raise HTTPException(404, "Position not found")
    return pos


def _booking_to_out(booking: Booking) -> BookingOut:
    return BookingOut(
        id=booking.id,
        slot_id=booking.slot_id,
        position_id=booking.position_id,
        position_title=booking.position.title if booking.position else None,
        applicant_name=booking.applicant_name,
        applicant_email=booking.applicant_email,
        applicant_phone=booking.applicant_phone,
        slot_date=booking.slot_date,
        start_time=booking.start_time,
        end_time=booking.end_time,
        status=booking.status,
        google_meet_link=booking.google_meet_link,
        google_calendar_event_id=booking.google_calendar_event_id,
        booked_at=booking.booked_at,
        updated_at=booking.updated_at,
    )


# ─────────────────────────────────────────────────────────────
# Public – applicant lookup by email
# ─────────────────────────────────────────────────────────────

@router.get("/by-email", response_model=BookingByEmailOut)
async def get_booking_by_email(
        email: str = Query(...),
        db: AsyncSession = Depends(get_db),
):
    await _auto_complete_expired_bookings(db)

    booking = await _get_active_booking_for_email(db, email)
    if not booking:
        raise HTTPException(404, "No active booking found for this email")

    return BookingByEmailOut(
        booking_id=booking.id,
        slot_id=booking.slot_id,
        position_id=booking.position_id,
        slot_date=booking.slot_date,
        start_time=booking.start_time,
        end_time=booking.end_time,
        position_title=booking.position.title if booking.position else None,
        applicant_name=booking.applicant_name,
        applicant_phone=booking.applicant_phone,
    )


# ─────────────────────────────────────────────────────────────
# Public – check cooldown status for applicant
# ─────────────────────────────────────────────────────────────

@router.get("/cooldown-status", tags=["Bookings"])
async def check_cooldown_status(
        email: str = Query(...),
        db: AsyncSession = Depends(get_db),
):
    """檢查應徵者是否在 30 天冷卻期中。"""
    email_lower = email.lower()

    completed_result = await db.execute(
        select(Booking).where(
            Booking.applicant_email == email_lower,
            Booking.status == BookingStatus.auto_completed,
        ).order_by(Booking.slot_date.desc(), Booking.end_time.desc())
    )
    last_completed = completed_result.scalars().first()

    today = get_taipei_date_and_time()[0]

    if not last_completed or not last_completed.slot_date:
        return {
            "email": email_lower,
            "in_cooldown": False,
            "last_interview_date": None,
            "available_date": None,
            "days_remaining": None,
            "message": "可以預約",
        }

    cooldown_end_date = last_completed.slot_date + timedelta(days=30)

    if today < cooldown_end_date:
        days_remaining = (cooldown_end_date - today).days
        return {
            "email": email_lower,
            "in_cooldown": True,
            "last_interview_date": str(last_completed.slot_date),
            "available_date": str(cooldown_end_date),
            "days_remaining": days_remaining,
            "message": f"請於 {cooldown_end_date} 後進行預約",
        }

    return {
        "email": email_lower,
        "in_cooldown": False,
        "last_interview_date": str(last_completed.slot_date),
        "available_date": str(cooldown_end_date),
        "days_remaining": 0,
        "message": "可以預約",
    }


# ─────────────────────────────────────────────────────────────
# Public – create booking (applicant)
# ─────────────────────────────────────────────────────────────

@router.post("", response_model=BookingOut)
async def create_booking(
        body: BookingCreate,
        db: AsyncSession = Depends(get_db),
):
    email_lower = body.email.lower()

    # ── 1. Block if already has an active booking ──────────────
    existing = await _get_active_booking_for_email(db, email_lower)
    if existing:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "DUPLICATE_BOOKING",
                "message": "You already have an active booking.",
                "booking_id": str(existing.id),
                "position_title": existing.position.title if existing.position else None,
                "slot_date": str(existing.slot_date) if existing.slot_date else None,
                "start_time": existing.start_time.strftime("%H:%M:%S") if isinstance(existing.start_time,
                                                                                     dt_time) else existing.start_time,
                "end_time": existing.end_time.strftime("%H:%M:%S") if isinstance(existing.end_time,
                                                                                 dt_time) else existing.end_time,
            },
        )

    # ── 2. 30-day cooldown after auto_completed ─────────────────
    completed_result = await db.execute(
        select(Booking).where(
            Booking.applicant_email == email_lower,
            Booking.status == BookingStatus.auto_completed,
        ).order_by(Booking.slot_date.desc(), Booking.end_time.desc())
    )
    last_completed = completed_result.scalars().first()

    if last_completed and last_completed.slot_date:
        cooldown_end_date = last_completed.slot_date + timedelta(days=30)
        today = get_taipei_date_and_time()[0]

        if today < cooldown_end_date:
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "COOLDOWN_PERIOD",
                    "message": f"您已於{last_completed.slot_date}完成面試，請於{cooldown_end_date}重新預約",
                    "last_interview_date": str(last_completed.slot_date),
                    "available_date": str(cooldown_end_date),
                    "interview_date_formatted": str(last_completed.slot_date),
                    "available_date_formatted": str(cooldown_end_date)
                }
            )

    slot = await _get_slot_or_404(db, body.slot_id)
    position = await _get_position_or_404(db, body.position_id)

    # ── 3. Slot availability check ──────────────────────────────
    if not slot_is_bookable(slot):
        raise HTTPException(400, "This slot is no longer available")

    # ── 3.5 Validate slot is not in the past ────────────────────
    current_date, current_time = get_taipei_date_and_time()
    slot_datetime = datetime.combine(slot.slot_date, slot.start_time)
    current_datetime = datetime.combine(current_date, current_time)

    if slot_datetime < current_datetime:
        raise HTTPException(
            status_code=400,
            detail="所選時段已過期。請選擇現在或現在以後的時段。"
        )

    if slot.booked_count >= slot.max_capacity:
        raise HTTPException(400, "This slot is fully booked")

    if not await is_slot_available_for_position(db, slot.id, position.id):
        raise HTTPException(
            400,
            "此時段已依職務限制規則被預約，請選擇其他時段",
        )

    # ── 4. Create booking ───────────────────────────────────────
    booking = Booking(
        slot_id=slot.id,
        position_id=position.id,
        applicant_name=body.name,
        applicant_email=email_lower,
        applicant_phone=body.phone,
        slot_date=slot.slot_date,
        start_time=slot.start_time,
        end_time=slot.end_time,
        status=BookingStatus.completed,
    )
    db.add(booking)

    slot.booked_count += 1
    if slot.booked_count >= slot.max_capacity:
        slot.status = SlotStatus.closed
    else:
        slot.status = SlotStatus.open

    await db.flush()

    # ── 5. Google Calendar + Meet ───────────────────────────────
    start_str = f"{slot.slot_date.strftime('%Y-%m-%d')} {slot.start_time.strftime('%H:%M')}"
    end_str = f"{slot.slot_date.strftime('%Y-%m-%d')} {slot.end_time.strftime('%H:%M')}"

    meet_link, event_id = google_calendar_manager.create_event(
        summary=f"Interview: {body.name} – {position.title}",
        start=start_str,
        end=end_str,
        description=f"Applicant: {body.name}\nEmail: {email_lower}\nPosition: {position.title}",
        attendees=[email_lower],
        add_meet=True,
        send_ical=True,
    )
    booking.google_meet_link = meet_link
    booking.google_calendar_event_id = event_id

    await db.commit()
    await db.refresh(booking)

    # ── 6. Send invitation email ────────────────────────────────
    import asyncio
    asyncio.create_task(
        email_service.send_interview_invitation(
            to_email=email_lower,
            applicant_name=body.name,
            position_title=position.title,
            slot_date=slot.slot_date,
            start_time=slot.start_time,
            end_time=slot.end_time,
            meet_link=meet_link,
            company_name=position.company,
        )
    )

    # ── 7. Send ICS invitation to internal attendees ────────────
    internal_attendees = _get_internal_attendees(position.title)
    if internal_attendees:
        asyncio.create_task(
            email_service.send_interviewer_ics_invitation(
                interviewer_emails=internal_attendees,
                applicant_name=body.name,
                position_title=position.title,
                slot_date=slot.slot_date,
                start_time=slot.start_time,
                end_time=slot.end_time,
                meet_link=meet_link,
                event_id=event_id,
            )
        )

    # ── 8. Notify HR of new booking ─────────────────────────────
    asyncio.create_task(
        email_service.send_booking_status_notification(
            event_type="new_booking",
            applicant_name=body.name,
            applicant_email=email_lower,
            position_title=position.title,
            slot_date=slot.slot_date,
            start_time=slot.start_time,
            end_time=slot.end_time,
        )
    )

    result = await db.execute(
        select(Booking)
        .options(joinedload(Booking.position))
        .where(Booking.id == booking.id)
    )
    booking = result.scalar_one()
    return _booking_to_out(booking)


# ─────────────────────────────────────────────────────────────
# Public – modify booking (applicant)
# ─────────────────────────────────────────────────────────────

@router.post("/modify", response_model=BookingOut)
async def modify_booking(
        body: BookingModify,
        db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Booking)
        .options(joinedload(Booking.position), joinedload(Booking.slot))
        .where(Booking.id == body.booking_id)
    )
    booking = result.scalar_one_or_none()
    if not booking:
        raise HTTPException(404, "Booking not found")

    if booking.applicant_email.lower() != body.email.lower():
        raise HTTPException(403, "Email does not match the booking")

    if booking.status not in (BookingStatus.completed,):
        if booking.status == BookingStatus.auto_completed and booking.slot_date:
            cooldown_end_date = booking.slot_date + timedelta(days=30)
            raise HTTPException(
                status_code=403,
                detail=f"您已於{booking.slot_date}完成面試，請於{cooldown_end_date}重新預約",
            )
        elif booking.status == BookingStatus.canceled:
            raise HTTPException(status_code=403, detail="預約已取消，請建立新預約")
        elif booking.status == BookingStatus.no_show:
            raise HTTPException(status_code=403, detail="您已標記為缺席，無法修改。請聯絡 HR 重新安排")
        else:
            raise HTTPException(400, "只有進行中的預約可以修改")

    new_slot = await _get_slot_or_404(db, body.slot_id)
    new_position = await _get_position_or_404(db, body.position_id)

    slot_changed = str(booking.slot_id) != str(body.slot_id)

    # ── 偵測應徵者實際改了哪些欄位（用於歷史紀錄摘要 + 通知信內容） ──
    old_position_title = booking.position.title if booking.position else ""
    changed_fields: list[str] = []
    if booking.applicant_name != body.name:
        changed_fields.append("姓名")
    if booking.applicant_phone != body.phone:
        changed_fields.append("電話")
    if str(booking.position_id) != str(body.position_id):
        changed_fields.append("應徵職位")
    if slot_changed:
        changed_fields.append("面試時段")

    # ── 寫入歷史快照（在任何修改之前，記錄「修改前」的完整狀態） ──
    # changed_by 標記為應徵者本人的 email，並加上前綴與 HR 編輯區分來源
    history_entry = BookingHistory(
        booking_id=booking.id,
        slot_id=booking.slot_id,
        position_id=booking.position_id,
        applicant_name=booking.applicant_name,
        applicant_email=booking.applicant_email,
        applicant_phone=booking.applicant_phone,
        slot_date=booking.slot_date,
        start_time=booking.start_time,
        end_time=booking.end_time,
        status_before=booking.status,
        status_after=booking.status,  # 應徵者自行修改不會變更狀態
        google_meet_link=booking.google_meet_link,
        google_calendar_event_id=booking.google_calendar_event_id,
        changed_by=f"applicant:{booking.applicant_email.lower()}",
    )
    db.add(history_entry)

    if slot_changed:
        if not slot_is_bookable(new_slot):
            raise HTTPException(400, "New slot is not available")

        current_date, current_time = get_taipei_date_and_time()
        slot_datetime = datetime.combine(new_slot.slot_date, new_slot.start_time)
        current_datetime = datetime.combine(current_date, current_time)

        if slot_datetime < current_datetime:
            raise HTTPException(status_code=400, detail="所選時段已過期。請選擇現在或現在以後的時段。")

        if new_slot.booked_count >= new_slot.max_capacity:
            raise HTTPException(400, "New slot is fully booked")

        if not await is_slot_available_for_position(
                db,
                new_slot.id,
                new_position.id,
                exclude_booking_id=booking.id,
        ):
            raise HTTPException(
                400,
                "此時段已依職務限制規則被預約，請選擇其他時段",
            )

    old_slot = booking.slot

    booking.slot_id = new_slot.id if new_slot else booking.slot_id
    booking.position_id = new_position.id
    booking.applicant_name = body.name
    booking.applicant_email = body.email.lower()
    booking.applicant_phone = body.phone

    if slot_changed:
        booking.slot_date = new_slot.slot_date
        booking.start_time = new_slot.start_time
        booking.end_time = new_slot.end_time

    if slot_changed and old_slot:
        old_slot.booked_count = max(0, old_slot.booked_count - 1)
        if old_slot.booked_count < old_slot.max_capacity:
            old_slot.status = SlotStatus.open

    if slot_changed:
        new_slot.booked_count += 1
        if new_slot.booked_count >= new_slot.max_capacity:
            new_slot.status = SlotStatus.closed
        else:
            new_slot.status = SlotStatus.open

    if slot_changed:
        meet_link, event_id = google_calendar_manager.recreate_meet_link(
            event_id=booking.google_calendar_event_id or "",
            applicant_name=body.name,
            applicant_email=body.email.lower(),
            position_title=new_position.title,
            slot_date=booking.slot_date,
            start_time=booking.start_time,
            end_time=booking.end_time,
        )
        booking.google_meet_link = meet_link
        booking.google_calendar_event_id = event_id
    else:
        if booking.google_calendar_event_id:
            summary = f"Interview: {body.name} – {new_position.title}"
            description = f"Applicant: {body.name}\nEmail: {body.email.lower()}\nPosition: {new_position.title}"

            meet_link, event_id = google_calendar_manager.update_event(
                event_id=booking.google_calendar_event_id,
                summary=summary,
                description=description,
                attendees=[body.email.lower()],
                send_ical=True,
                add_meet=True,
            )
            if event_id:
                booking.google_calendar_event_id = event_id
            if meet_link:
                booking.google_meet_link = meet_link

    await db.commit()
    await db.refresh(booking)

    if slot_changed:
        import asyncio
        asyncio.create_task(
            email_service.send_interview_invitation(
                to_email=body.email.lower(),
                applicant_name=body.name,
                position_title=new_position.title,
                slot_date=booking.slot_date,
                start_time=booking.start_time,
                end_time=booking.end_time,
                meet_link=booking.google_meet_link,
                is_update=True,
                company_name=new_position.company,
            )
        )

    # ── 應徵者自行修改資料時，無論是否換場次，都通知 HR ──────────
    if changed_fields:
        import asyncio
        asyncio.create_task(
            email_service.send_applicant_modified_notification(
                applicant_name=body.name,
                applicant_email=body.email.lower(),
                position_title=new_position.title,
                slot_date=booking.slot_date,
                start_time=booking.start_time,
                end_time=booking.end_time,
                changed_fields=changed_fields,
                old_position_title=old_position_title,
            )
        )

    result = await db.execute(
        select(Booking)
        .options(joinedload(Booking.position))
        .where(Booking.id == booking.id)
    )
    booking_out = result.scalar_one()
    return _booking_to_out(booking_out)


# ─────────────────────────────────────────────────────────────
# HR – list all bookings
# ─────────────────────────────────────────────────────────────

@router.get("", response_model=List[BookingOut])
async def list_bookings(
        db: AsyncSession = Depends(get_db),
        _hr: str = Depends(get_current_hr),
        skip: int = Query(0, ge=0),
        limit: int = Query(50, ge=1, le=500),
):
    await _auto_complete_expired_bookings(db)

    result = await db.execute(
        select(Booking)
        .options(
            joinedload(Booking.position),
            joinedload(Booking.slot)
        )
        .order_by(Booking.booked_at.desc())
        .offset(skip)
        .limit(limit)
    )
    bookings = result.unique().scalars().all()
    return [_booking_to_out(b) for b in bookings]


# ─────────────────────────────────────────────────────────────
# HR – booking edit options
# ─────────────────────────────────────────────────────────────

@router.get("/edit-options", response_model=BookingEditOptions)
async def get_booking_edit_options(
        db: AsyncSession = Depends(get_db),
        _hr: str = Depends(get_current_hr),
):
    pos_result = await db.execute(
        select(Position)
        .where(Position.is_active == True)
        .order_by(Position.title)
    )

    current_date, current_time = get_taipei_date_and_time()

    slot_result = await db.execute(
        select(InterviewSlot)
        .where(
            (InterviewSlot.slot_date > current_date) |
            ((InterviewSlot.slot_date == current_date) & (InterviewSlot.start_time >= current_time))
        )
        .order_by(InterviewSlot.slot_date, InterviewSlot.start_time)
    )
    return BookingEditOptions(
        positions=[PositionOut.model_validate(p) for p in pos_result.scalars().all()],
        slots=[SlotOut.model_validate(s) for s in slot_result.scalars().all()],
    )


# ─────────────────────────────────────────────────────────────
# HR – update a booking
# 修正：先快照 event_id，再改信箱，刪除時用快照 event_id
#       若刪除失敗則保留 event_id 在 DB，方便重試
# ─────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────
# HR – get booking edit history
# ─────────────────────────────────────────────────────────────

@router.get("/{booking_id}/history", response_model=List[BookingHistoryOut])
async def get_booking_history(
        booking_id: UUID,
        db: AsyncSession = Depends(get_db),
        _hr: str = Depends(get_current_hr),
):
    """回傳指定預約的所有編輯歷史（由舊到新）。"""
    result = await db.execute(
        select(BookingHistory)
        .where(BookingHistory.booking_id == booking_id)
        .order_by(BookingHistory.changed_at.asc())
    )
    rows = result.scalars().all()
    return rows


@router.patch("/{booking_id}", response_model=BookingOut)
async def hr_update_booking(
        booking_id: UUID,
        body: BookingHRUpdate,
        db: AsyncSession = Depends(get_db),
        _hr: str = Depends(get_current_hr),
):
    result = await db.execute(
        select(Booking)
        .options(joinedload(Booking.position), joinedload(Booking.slot))
        .where(Booking.id == booking_id)
    )
    booking = result.scalar_one_or_none()
    if not booking:
        raise HTTPException(404, "Booking not found")

    # ── 【修正關鍵】先快照原始 event_id，避免改信箱後再刪除時出現 attendee 不符問題 ──
    original_event_id = booking.google_calendar_event_id

    # 先記錄變更前資訊
    old_status = booking.status
    slot_time_changed = False

    # ── 寫入歷史快照（在任何修改之前） ──────────────────────────
    history_entry = BookingHistory(
        booking_id=booking.id,
        slot_id=booking.slot_id,
        position_id=booking.position_id,
        applicant_name=booking.applicant_name,
        applicant_email=booking.applicant_email,
        applicant_phone=booking.applicant_phone,
        slot_date=booking.slot_date,
        start_time=booking.start_time,
        end_time=booking.end_time,
        status_before=booking.status,
        status_after=body.status if body.status is not None else booking.status,
        google_meet_link=booking.google_meet_link,
        google_calendar_event_id=booking.google_calendar_event_id,
        changed_by=_hr,
    )
    db.add(history_entry)

    if body.slot_date is not None:
        booking.slot_date = body.slot_date
        slot_time_changed = True
    if body.start_time is not None:
        booking.start_time = _parse_time(body.start_time)
        slot_time_changed = True
    if body.end_time is not None:
        booking.end_time = _parse_time(body.end_time)
        slot_time_changed = True

    # 【修正】 HR 修改時間後\uff0c重新對應到正確的 InterviewSlot\uff0c並同步 booked_count
    # 當 HR 在「查看預約」直接輸入新日期/時間時\uff0cbooking.slot_id 並不會自動改變\uff0c
    # 導致 booked_count 停留在舊 slot\uff0c日曆永遠顯示舊紀錄。
    if slot_time_changed and booking.status not in (BookingStatus.canceled,):
        new_slot_date = booking.slot_date
        new_start = booking.start_time
        matched_slot_result = await db.execute(
            select(InterviewSlot).where(
                InterviewSlot.slot_date == new_slot_date,
                InterviewSlot.start_time == new_start,
            )
        )
        matched_slot = matched_slot_result.scalar_one_or_none()

        old_slot_id = booking.slot_id
        new_slot_id = matched_slot.id if matched_slot else None

        if str(old_slot_id) != str(new_slot_id):
            if old_slot_id:
                old_slot_result = await db.execute(
                    select(InterviewSlot).where(InterviewSlot.id == old_slot_id)
                )
                old_slot_obj = old_slot_result.scalar_one_or_none()
                if old_slot_obj:
                    old_slot_obj.booked_count = max(0, old_slot_obj.booked_count - 1)
                    if old_slot_obj.booked_count < old_slot_obj.max_capacity:
                        old_slot_obj.status = SlotStatus.open

            if matched_slot:
                matched_slot.booked_count += 1
                if matched_slot.booked_count >= matched_slot.max_capacity:
                    matched_slot.status = SlotStatus.closed

            booking.slot_id = new_slot_id

    # 更新基本標量欄位
    if body.applicant_name is not None:
        booking.applicant_name = body.applicant_name
    if body.applicant_email is not None:
        booking.applicant_email = body.applicant_email.lower()
    if body.applicant_phone is not None:
        booking.applicant_phone = body.applicant_phone
    if body.google_meet_link is not None:
        booking.google_meet_link = body.google_meet_link

    # 先套用狀態更新
    if body.status is not None:
        booking.status = body.status

    # 更新職位
    if body.position_id is not None:
        new_pos = await _get_position_or_404(db, body.position_id)
        booking.position_id = new_pos.id
    else:
        new_pos = booking.position

    send_email = slot_time_changed
    import asyncio

    # ── 檢測狀態是否變更為 canceled ──
    status_is_cancelled = booking.status == BookingStatus.canceled
    status_changed_to_cancelled = (
            status_is_cancelled
            and old_status != BookingStatus.canceled
    )
    status_changed_to_no_show = (
            body.status == BookingStatus.no_show
            and old_status != BookingStatus.no_show
    )

    # ── 只要新狀態為非 active（不論舊狀態），且有 event_id，就刪除 Google 日曆行程 ──
    # 修正：舊狀態為 no_show 時直接改 canceled，原本條件不觸發導致行程殘留
    status_changed_away_from_active = (
            body.status is not None
            and body.status != old_status
            and body.status not in (BookingStatus.completed, BookingStatus.auto_completed)
    )

    # 需求保證：狀態「改成 canceled」時，一定進入刪除 Google Calendar 行程流程
    # 兜底條件：只要最終狀態為 canceled 且仍有 event_id，不論舊狀態為何一律刪除
    # （避免前次刪除失敗殘留 event_id、或 old_status 已是 canceled 等邊界情況）
    status_is_canceled_with_event = (
        booking.status == BookingStatus.canceled and bool(original_event_id)
    )
    if (
        status_changed_to_cancelled
        or status_changed_to_no_show
        or status_changed_away_from_active
        or status_is_canceled_with_event
    ):
        # 診斷 log：記錄觸發原因，方便排查行程未刪問題
        logger.info(
            f"Calendar delete triggered for booking {booking_id}. "
            f"original_event_id={original_event_id}, old_status={old_status}, "
            f"new_status={body.status}, "
            f"is_cancelled={status_is_cancelled}, "
            f"to_cancelled={status_changed_to_cancelled}, "
            f"to_no_show={status_changed_to_no_show}, "
            f"away_from_active={status_changed_away_from_active}"
        )

        # 【修正】使用快照的 original_event_id，確保信箱修改後仍能刪除正確的行程
        calendar_deleted = False
        if original_event_id:
            try:
                delete_success = google_calendar_manager.delete_event(
                    event_id=original_event_id,
                    send_ical=False,  # 信箱可能已改，不依賴 attendee 驗證
                )
                # 【修正】delete_event() 只 catch HttpError 並回傳 False，
                # 沒有 raise 不代表刪除成功，必須檢查回傳值
                if delete_success:
                    calendar_deleted = True
                    logger.info(
                        f"Calendar event {original_event_id} force deleted successfully "
                        f"due to status change: {old_status} → {booking.status}"
                    )
                else:
                    logger.error(
                        f"[CRITICAL] delete_event returned False for {original_event_id} "
                        f"(status change: {old_status} → {booking.status})"
                    )
            except Exception as e:
                # 【修正】記錄完整 traceback，讓問題可被追蹤
                logger.error(
                    f"[CRITICAL] Failed to delete calendar event {original_event_id}: "
                    f"{type(e).__name__}: {e}",
                    exc_info=True,
                )

            # 【修正】只有確認刪除成功才清除 DB 裡的 event_id
            # 若刪除失敗，保留 event_id 讓 HR 可以手動重試
            if calendar_deleted:
                booking.google_calendar_event_id = None
                booking.google_meet_link = None
            else:
                logger.warning(
                    f"Booking {booking_id} status changed to {booking.status} but "
                    f"calendar event {original_event_id} was NOT deleted. "
                    f"HR should manually remove this event."
                )
        else:
            # 原本就沒有 event_id，直接清除 meet link
            calendar_deleted = True  # 視為成功（無行程可刪）
            booking.google_meet_link = None
            logger.info(
                f"Booking {booking_id} has no google_calendar_event_id; skipping calendar delete."
            )

        send_email = False  # 取消或缺席不發送正常面試通知信

        # 如果狀態是變更為 canceled，則釋放時段額度
        if status_changed_to_cancelled and old_status in (BookingStatus.completed, BookingStatus.auto_completed,
                                                          BookingStatus.no_show):
            if booking.slot:
                booking.slot.booked_count = max(0, booking.slot.booked_count - 1)
                if booking.slot.booked_count < booking.slot.max_capacity:
                    booking.slot.status = SlotStatus.open

    # ── 檢測狀態是否從其他狀態變更為 confirmed(completed) ──
    status_changed_to_confirmed = (
            body.status == BookingStatus.completed
            and old_status != BookingStatus.completed
    )
    should_recreate_from_legacy_status = (
            body.status == BookingStatus.completed
            and old_status in (BookingStatus.auto_completed, BookingStatus.no_show)
    )

    # ── 狀態變為 confirmed 時，一律重新建立 Google Calendar 行程 + Meet 連結 ──
    if status_changed_to_confirmed:
        if should_recreate_from_legacy_status and original_event_id:
            try:
                delete_success = google_calendar_manager.delete_event(
                    event_id=original_event_id,
                    send_ical=False,
                )
                # 【修正】delete_event() 回傳 False 時不能當作成功繼續往下重建行程
                if not delete_success:
                    raise RuntimeError(
                        f"delete_event returned False for {original_event_id}"
                    )
                booking.google_calendar_event_id = None
                booking.google_meet_link = None
                logger.info(
                    f"Removed original calendar event {original_event_id} for booking {booking_id} "
                    f"before recreating confirmed event"
                )
            except Exception as e:
                logger.error(
                    f"[CRITICAL] Failed to remove original calendar event {original_event_id} "
                    f"before recreating confirmed event for booking {booking_id}: "
                    f"{type(e).__name__}: {e}",
                    exc_info=True,
                )
                raise HTTPException(
                    status_code=500,
                    detail="Failed to remove original calendar event before recreating confirmed event",
                )

        pos_title = new_pos.title if new_pos else ""
        start_str = f"{booking.slot_date.strftime('%Y-%m-%d')} {booking.start_time.strftime('%H:%M')}"
        end_str = f"{booking.slot_date.strftime('%Y-%m-%d')} {booking.end_time.strftime('%H:%M')}"

        try:
            meet_link, event_id = google_calendar_manager.create_event(
                summary=f"面試邀約: {booking.applicant_name} – {pos_title}",
                start=start_str,
                end=end_str,
                description=f"Applicant: {booking.applicant_name}\nEmail: {booking.applicant_email}\nPosition: {pos_title}",
                attendees=[booking.applicant_email],
                add_meet=True,
                send_ical=True,
            )
            booking.google_meet_link = meet_link
            booking.google_calendar_event_id = event_id
            logger.info(
                f"Booking {booking_id} status changed to confirmed; "
                f"new calendar event created: {event_id}, meet_link={meet_link}"
            )
        except Exception as e:
            logger.error(
                f"[CRITICAL] Failed to create calendar event for booking {booking_id} "
                f"on status change to confirmed: {type(e).__name__}: {e}",
                exc_info=True,
            )

        send_email = True  # 重新確認面試時發送通知信

    # ── 如果是純修改時間且狀態並非取消/缺席（避免與上方取消刪除流程衝突重建行程） ──
    # 【修正】症狀：同一次 PATCH 同時帶 status=canceled 與時間變更時，
    # 上方 if 區塊已正確刪除行程，但這裡因為是獨立的 if/elif 鏈（配對對象是
    # status_changed_to_confirmed，不是上方的取消判斷），slot_time_changed 仍為 True，
    # 導致刪除後又重新建立一個新行程。加上 booking.status 仍為 active 的 guard 避免此情況。
    elif slot_time_changed and booking.status not in (
        BookingStatus.canceled,
        BookingStatus.no_show,
    ):
        pos_title = new_pos.title if new_pos else ""

        # 先刪除舊行程
        if original_event_id:
            try:
                delete_success = google_calendar_manager.delete_event(
                    event_id=original_event_id,
                    send_ical=False,
                )
                # 【修正】檢查回傳值，刪除失敗時不要清空 event_id，
                # 否則舊行程會在 Google Calendar 上孤兒化、無法再被追蹤刪除
                if delete_success:
                    booking.google_calendar_event_id = None
                    booking.google_meet_link = None
                    logger.info(f"Deleted old calendar event {original_event_id} due to slot time change")
                else:
                    logger.error(
                        f"[CRITICAL] delete_event returned False for {original_event_id} "
                        f"during slot time change; keeping event_id for manual retry"
                    )
            except Exception as e:
                logger.error(
                    f"Failed to delete old calendar event {original_event_id} during time change: "
                    f"{type(e).__name__}: {e}",
                    exc_info=True,
                )

        # 新建行程
        start_str = f"{booking.slot_date.strftime('%Y-%m-%d')} {booking.start_time.strftime('%H:%M')}"
        end_str = f"{booking.slot_date.strftime('%Y-%m-%d')} {booking.end_time.strftime('%H:%M')}"
        try:
            meet_link, event_id = google_calendar_manager.create_event(
                summary=f"面試邀約: {booking.applicant_name} – {pos_title}",
                start=start_str,
                end=end_str,
                description=f"Applicant: {booking.applicant_name}\nEmail: {booking.applicant_email}\nPosition: {pos_title}",
                attendees=[booking.applicant_email],
                add_meet=True,
                send_ical=True,
            )
            booking.google_meet_link = meet_link
            booking.google_calendar_event_id = event_id
            logger.info(
                f"Created new calendar event {event_id} after slot time change for booking {booking_id}"
            )
        except Exception as e:
            logger.error(
                f"Failed to create new calendar event for booking {booking_id} after time change: "
                f"{type(e).__name__}: {e}",
                exc_info=True,
            )
    # ── 如果只更改了個人基本資料（姓名/郵件/電話/職缺），且行程仍為 active，則原地更新日曆文字資訊 ──
    elif (
        original_event_id
        and booking.status in (BookingStatus.completed, BookingStatus.auto_completed)
        and (body.applicant_name or body.applicant_email or body.applicant_phone or body.position_id)
    ):
        pos_title = new_pos.title if new_pos else ""
        summary = f"面試邀約: {booking.applicant_name} – {pos_title}"
        description = f"Applicant: {booking.applicant_name}\nEmail: {booking.applicant_email}\nPosition: {pos_title}"

        asyncio.create_task(
            _update_calendar_event_async(
                event_id=original_event_id,  # 使用快照 event_id
                summary=summary,
                description=description,
                attendees=[booking.applicant_email],
            )
        )

    await db.commit()
    await db.refresh(booking)

    if send_email:
        asyncio.create_task(
            email_service.send_interview_invitation(
                to_email=booking.applicant_email,
                applicant_name=booking.applicant_name,
                position_title=new_pos.title if new_pos else "",
                slot_date=booking.slot_date,
                start_time=booking.start_time,
                end_time=booking.end_time,
                meet_link=booking.google_meet_link,
                is_update=True,
                company_name=new_pos.company if new_pos else None,
            )
        )

    # ── Notify HR when status changes to canceled / no_show / auto_completed ──
    _notify_statuses = {BookingStatus.canceled, BookingStatus.no_show, BookingStatus.auto_completed}
    if body.status is not None and body.status != old_status and body.status in _notify_statuses:
        asyncio.create_task(
            email_service.send_booking_status_notification(
                event_type="status_change",
                applicant_name=booking.applicant_name,
                applicant_email=booking.applicant_email,
                position_title=new_pos.title if new_pos else "",
                slot_date=booking.slot_date,
                start_time=booking.start_time,
                end_time=booking.end_time,
                new_status=booking.status.value,
                old_status=old_status.value,
            )
        )

    result = await db.execute(
        select(Booking)
        .options(joinedload(Booking.position))
        .where(Booking.id == booking_id)
    )
    booking = result.scalar_one()
    booking_out = _booking_to_out(booking)

    # 若狀態變更為非 active 但行程刪除失敗，在回應中附帶警告
    if (status_changed_to_cancelled or status_changed_to_no_show or status_changed_away_from_active or status_is_canceled_with_event):
        if original_event_id and not calendar_deleted:
            return {
                **booking_out.model_dump(),
                "calendar_delete_warning": (
                    f"預約狀態已更新，但 Google Calendar 行程（{original_event_id}）"
                    f"刪除失敗，請手動至 Google Calendar 移除。"
                ),
            }

    return booking_out


# ─────────────────────────────────────────────────────────────
# HR – delete a booking
# ─────────────────────────────────────────────────────────────

@router.delete("/{booking_id}")
async def hr_delete_booking(
        booking_id: UUID,
        db: AsyncSession = Depends(get_db),
        _hr: str = Depends(get_current_hr),
):
    result = await db.execute(
        select(Booking)
        .options(joinedload(Booking.slot), joinedload(Booking.position))
        .where(Booking.id == booking_id)
    )
    booking = result.scalar_one_or_none()
    if not booking:
        raise HTTPException(404, "Booking not found")

    if booking.slot:
        booking.slot.booked_count = max(0, booking.slot.booked_count - 1)
        if booking.slot.booked_count < booking.slot.max_capacity:
            booking.slot.status = SlotStatus.open

    if booking.google_calendar_event_id:
        try:
            google_calendar_manager.delete_event(
                event_id=booking.google_calendar_event_id,
                send_ical=True,
            )
        except Exception as e:
            logger.warning(f"Failed to delete calendar event during DB deletion: {e}")

    await db.delete(booking)
    await db.commit()
    return {"message": "Booking deleted"}


# ─────────────────────────────────────────────────────────────
# HR – export bookings to Excel
# ─────────────────────────────────────────────────────────────

@router.get("/export-file")
async def export_bookings(
        status: Optional[str] = None,
        position: Optional[str] = None,
        name: Optional[str] = None,
        email: Optional[str] = None,
        keyword: Optional[str] = None,
        db: AsyncSession = Depends(get_db),
        _hr: str = Depends(get_current_hr),
):
    q = (
        select(Booking)
        .options(joinedload(Booking.position))
        .order_by(Booking.booked_at.desc())
    )

    if status:
        q = q.where(Booking.status == status)
    if position:
        q = q.where(Booking.position.has(Position.title == position))
    if name:
        q = q.where(Booking.applicant_name == name)
    if email:
        q = q.where(Booking.applicant_email == email)
    if keyword:
        kw = f"%{keyword.lower()}%"
        q = q.where(
            or_(
                func.lower(Booking.applicant_name).like(kw),
                func.lower(Booking.applicant_email).like(kw),
            )
        )

    result = await db.execute(q)
    filtered = result.scalars().all()

    try:
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Bookings"

        headers = [
            "ID", "Name", "Email", "Phone", "Position",
            "Date", "Start", "End", "Status",
            "Google Meet Link", "Booked At",
        ]
        ws.append(headers)

        for b in filtered:
            ws.append([
                str(b.id),
                b.applicant_name,
                b.applicant_email,
                b.applicant_phone,
                b.position.title if b.position else "",
                str(b.slot_date) if b.slot_date else "",
                str(b.start_time)[:5] if b.start_time else "",
                str(b.end_time)[:5] if b.end_time else "",
                b.status,
                b.google_meet_link or "",
                b.booked_at.strftime("%Y-%m-%d %H:%M") if b.booked_at else "",
            ])

        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)

        return StreamingResponse(
            buf,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": "attachment; filename=bookings.xlsx"},
        )
    except ImportError:
        raise HTTPException(500, "openpyxl not installed; cannot export Excel")


# ─────────────────────────────────────────────────────────────
# Public – cancel interview (external endpoint) AppsScript 串接
# ─────────────────────────────────────────────────────────────

@router.post("/apps_script", tags=["Interview"])
async def cancel_interview(
        payload: InterviewPayload,
        db: AsyncSession = Depends(get_db),
):
    """
    取消應徵者的面試預約（透過外接 AppsScript 觸發）。
    """
    email_lower = payload.email.lower()
    import asyncio

    try:
        print(f"收到資料：")
        print(f"submit_time: {payload.submit_time}")
        print(f"email: {email_lower}")
        print(f"title: {payload.title}")
        print(f"canceled_result: {payload.canceled_result}")
        print(f"others_result: {payload.others_result}")

        result = await db.execute(
            select(Booking)
            .options(joinedload(Booking.position), joinedload(Booking.slot))
            .where(
                Booking.applicant_email == email_lower,
                Booking.status.in_([BookingStatus.completed, BookingStatus.auto_completed]),
            )
            .order_by(Booking.booked_at.desc())
        )
        booking = result.scalars().first()

        if not booking:
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "BOOKING_NOT_FOUND",
                    "message": f"未找到 {email_lower} 的有效面試預約",
                }
            )

        if booking.position and booking.position.title.lower() != payload.title.lower():
            logger.warning(
                f"Position title mismatch: expected {booking.position.title}, "
                f"got {payload.title} for {email_lower}"
            )

        old_status = booking.status
        booking.status = BookingStatus.canceled

        if booking.slot:
            booking.slot.booked_count = max(0, booking.slot.booked_count - 1)
            if booking.slot.booked_count < booking.slot.max_capacity:
                booking.slot.status = SlotStatus.open

        if booking.google_calendar_event_id:
            calendar_deleted = False
            try:
                delete_success = google_calendar_manager.delete_event(
                    event_id=booking.google_calendar_event_id,
                    send_ical=False,
                )
                # 【修正】檢查回傳值，避免 HttpError 被內部 catch 並回傳 False 時誤判成功
                if delete_success:
                    calendar_deleted = True
                    logger.info(f"Deleted calendar event {booking.google_calendar_event_id}")
                else:
                    logger.error(
                        f"[CRITICAL] delete_event returned False for "
                        f"{booking.google_calendar_event_id} (AppsScript cancel)"
                    )
            except Exception as e:
                logger.error(
                    f"[CRITICAL] Failed to delete calendar event {booking.google_calendar_event_id}: "
                    f"{type(e).__name__}: {e}",
                    exc_info=True,
                )

            if calendar_deleted:
                booking.google_calendar_event_id = None
                booking.google_meet_link = None
            else:
                logger.warning(
                    f"AppsScript cancel: booking {booking.id} canceled but "
                    f"calendar event was NOT deleted. Manual removal required."
                )

        await db.commit()
        await db.refresh(booking)

        logger.info(f"Successfully canceled interview for {email_lower}")

        # ── Notify HR that the applicant canceled ───────────────────
        asyncio.create_task(
            email_service.send_booking_status_notification(
                event_type="status_change",
                applicant_name=booking.applicant_name,
                applicant_email=booking.applicant_email,
                position_title=booking.position.title if booking.position else "",
                slot_date=booking.slot_date,
                start_time=booking.start_time,
                end_time=booking.end_time,
                new_status=BookingStatus.canceled.value,
                old_status=old_status.value,
            )
        )

        return {
            "status": "success",
            "message": "取消面試成功",
            "booking_id": str(booking.id),
            "email": email_lower,
            "position_title": booking.position.title if booking.position else None,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"錯誤: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="伺服器錯誤，請稍後再試")


# ─────────────────────────────────────────────────────────────
# HR – check and auto-complete expired bookings
# ─────────────────────────────────────────────────────────────

@router.post("/check-expired", tags=["Bookings"])
async def check_expired_bookings(
        db: AsyncSession = Depends(get_db),
        _hr: str = Depends(get_current_hr),
):
    try:
        current_date, current_time = get_taipei_date_and_time()
        logger.info(f"Checking for expired bookings at {current_date} {current_time} (Taiwan time)")

        result = await db.execute(
            select(Booking)
            .options(joinedload(Booking.position))
            .where(
                and_(
                    Booking.status == BookingStatus.completed,
                    Booking.slot_date.isnot(None),
                    Booking.end_time.isnot(None),
                    (
                            (Booking.slot_date < current_date) |
                            (
                                    (Booking.slot_date == current_date) &
                                    (Booking.end_time <= current_time)
                            )
                    )
                )
            )
            .order_by(Booking.slot_date, Booking.end_time)
        )

        expired_bookings = result.scalars().all()
        updated_bookings_info = []

        if expired_bookings:
            logger.info(f"Found {len(expired_bookings)} expired bookings to update")

            for booking in expired_bookings:
                old_status = booking.status
                booking.status = BookingStatus.auto_completed

                updated_bookings_info.append({
                    "booking_id": str(booking.id),
                    "applicant_email": booking.applicant_email,
                    "applicant_name": booking.applicant_name,
                    "position_title": booking.position.title if booking.position else None,
                    "interview_end_time": f"{booking.slot_date} {booking.end_time}",
                })

                logger.info(
                    f"Updated booking {booking.id} for {booking.applicant_email}: "
                    f"{old_status} → {booking.status} "
                    f"(Interview ended at {booking.slot_date} {booking.end_time})"
                )

            await db.commit()
            logger.info(f"Successfully updated {len(expired_bookings)} bookings to auto_completed")

            # ── Notify HR for each auto_completed booking ────────
            import asyncio
            for booking in expired_bookings:
                asyncio.create_task(
                    email_service.send_booking_status_notification(
                        event_type="status_change",
                        applicant_name=booking.applicant_name,
                        applicant_email=booking.applicant_email,
                        position_title=booking.position.title if booking.position else "",
                        slot_date=booking.slot_date,
                        start_time=booking.start_time,
                        end_time=booking.end_time,
                        new_status="auto_completed",
                        old_status="completed",
                    )
                )
        else:
            logger.info("No expired bookings found")

        return {
            "status": "success",
            "message": "檢查完成",
            "updated_count": len(expired_bookings),
            "updated_bookings": updated_bookings_info,
        }

    except Exception as e:
        logger.error(f"Error checking expired bookings: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="伺服器錯誤，請稍後再試")