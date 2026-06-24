"""Slot capacity helpers (no per-position keyword rules)."""
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import Booking, BookingStatus, InterviewSlot, SlotStatus

_ACTIVE_BOOKING_STATUSES = (
    BookingStatus.completed,
    BookingStatus.auto_completed,
    BookingStatus.no_show,
)


def slot_has_remaining_capacity(slot: InterviewSlot) -> bool:
    return slot.booked_count < slot.max_capacity


def slot_is_hr_closed_empty(slot: InterviewSlot) -> bool:
    """HR manually closed and nobody has booked yet."""
    return slot.status == SlotStatus.closed and slot.booked_count == 0


def slot_is_bookable(slot: InterviewSlot) -> bool:
    if not slot_has_remaining_capacity(slot):
        return False
    if slot_is_hr_closed_empty(slot):
        return False
    return True


def open_only_slots_filter():
    """
    Applicant listing: only remaining capacity matters.
    No filtering by position title (產品/研發/etc.).
    """
    return and_(
        InterviewSlot.booked_count < InterviewSlot.max_capacity,
        or_(
            InterviewSlot.status == SlotStatus.open,
            InterviewSlot.booked_count > 0,
        ),
    )


def future_slots_filter(current_date, current_time):
    """Hide slots once their start time has passed (no longer bookable after it begins)."""
    return (InterviewSlot.slot_date > current_date) | (
        (InterviewSlot.slot_date == current_date)
        & (InterviewSlot.start_time > current_time)
    )


async def _count_active_bookings(db: AsyncSession, slot_id) -> int:
    result = await db.execute(
        select(func.count())
        .select_from(Booking)
        .where(
            Booking.slot_id == slot_id,
            Booking.status.in_(_ACTIVE_BOOKING_STATUSES),
        )
    )
    return int(result.scalar_one() or 0)


async def repair_slots_state(db: AsyncSession) -> tuple[int, int]:
    """
    1. Sync booked_count from real active bookings (fixes legacy exclusive rules).
    2. Re-open slots that still have capacity.
    Returns (synced_slots, reopened_slots).
    """
    slots = (await db.execute(select(InterviewSlot))).scalars().all()
    synced = 0
    for slot in slots:
        actual = await _count_active_bookings(db, slot.id)
        if slot.booked_count != actual:
            slot.booked_count = actual
            synced += 1
        if actual >= slot.max_capacity:
            slot.status = SlotStatus.closed
        elif actual > 0:
            slot.status = SlotStatus.open

    result = await db.execute(
        update(InterviewSlot)
        .where(
            InterviewSlot.booked_count < InterviewSlot.max_capacity,
            InterviewSlot.status == SlotStatus.closed,
            InterviewSlot.booked_count > 0,
        )
        .values(status=SlotStatus.open)
    )
    await db.commit()
    return synced, result.rowcount or 0


# Backward-compatible alias
async def repair_misclosed_slots(db: AsyncSession) -> int:
    _, reopened = await repair_slots_state(db)
    return reopened
