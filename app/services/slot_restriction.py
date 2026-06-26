"""
Position / group slot visibility rules for applicant booking.

- Default (position not in a multi-position group): no restriction, capacity controlled by max_capacity.
- Multi-position rule: any active booking by a group member hides the slot for all members.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.models import (
    Booking,
    BookingStatus,
    SlotRestrictionRule,
)

_ACTIVE_BOOKING_STATUSES = (
    BookingStatus.completed,
    BookingStatus.auto_completed,
    BookingStatus.no_show,
)


@dataclass
class _RuleInfo:
    rule_id: UUID
    position_ids: Set[UUID]
    is_group: bool


@dataclass
class RestrictionIndex:
    """Cached rule map built once per request."""
    position_to_rules: Dict[UUID, List[_RuleInfo]] = field(default_factory=dict)


def _rule_blocks(
    rule: _RuleInfo,
    booked_position_ids: Set[UUID],
    position_id: UUID,
) -> bool:
    if rule.is_group:
        return bool(booked_position_ids & rule.position_ids)
    return position_id in booked_position_ids


async def load_restriction_index(db: AsyncSession) -> RestrictionIndex:
    result = await db.execute(
        select(SlotRestrictionRule).options(
            selectinload(SlotRestrictionRule.positions)
        )
    )
    rules = result.scalars().all()
    index = RestrictionIndex()

    for rule in rules:
        pos_ids = {rp.position_id for rp in rule.positions}
        if not pos_ids:
            continue
        info = _RuleInfo(rule_id=rule.id, position_ids=pos_ids, is_group=len(pos_ids) > 1)
        for pid in pos_ids:
            index.position_to_rules.setdefault(pid, []).append(info)

    return index


async def get_slot_booked_position_ids(
    db: AsyncSession,
    slot_id: UUID,
    *,
    exclude_booking_id: Optional[UUID] = None,
) -> Set[UUID]:
    q = select(Booking.position_id).where(
        Booking.slot_id == slot_id,
        Booking.status.in_(_ACTIVE_BOOKING_STATUSES),
        Booking.position_id.isnot(None),
    )
    if exclude_booking_id:
        q = q.where(Booking.id != exclude_booking_id)
    result = await db.execute(q)
    return {row[0] for row in result.all() if row[0]}


def slot_blocked_for_position(
    booked_position_ids: Set[UUID],
    position_id: UUID,
    index: RestrictionIndex,
) -> bool:
    """
    Return True when the slot must be hidden / rejected for this position.
    """
    if not booked_position_ids:
        return False

    rules = index.position_to_rules.get(position_id, [])
    if rules:
        return any(
            _rule_blocks(rule, booked_position_ids, position_id) for rule in rules
        )

    # 沒有設定任何 rule 的職位：不做限制，由 slot max_capacity 控制名額
    return False


async def is_slot_available_for_position(
    db: AsyncSession,
    slot_id: UUID,
    position_id: UUID,
    index: Optional[RestrictionIndex] = None,
    *,
    exclude_booking_id: Optional[UUID] = None,
) -> bool:
    if index is None:
        index = await load_restriction_index(db)
    booked = await get_slot_booked_position_ids(
        db, slot_id, exclude_booking_id=exclude_booking_id
    )
    return not slot_blocked_for_position(booked, position_id, index)


async def filter_slots_for_position(
    db: AsyncSession,
    slot_ids: List[UUID],
    position_id: UUID,
    index: Optional[RestrictionIndex] = None,
) -> Set[UUID]:
    """Return slot IDs that are still bookable for the given position."""
    if not slot_ids:
        return set()
    if index is None:
        index = await load_restriction_index(db)

    result = await db.execute(
        select(Booking.slot_id, Booking.position_id).where(
            Booking.slot_id.in_(slot_ids),
            Booking.status.in_(_ACTIVE_BOOKING_STATUSES),
            Booking.position_id.isnot(None),
        )
    )
    by_slot: Dict[UUID, Set[UUID]] = {}
    for slot_id, pos_id in result.all():
        if slot_id and pos_id:
            by_slot.setdefault(slot_id, set()).add(pos_id)

    allowed: Set[UUID] = set()
    for sid in slot_ids:
        booked = by_slot.get(sid, set())
        if not slot_blocked_for_position(booked, position_id, index):
            allowed.add(sid)
    return allowed
