"""
app/models/models.py  –  All PostgreSQL table definitions (English only)
"""
import uuid
from datetime import datetime, date, time
from sqlalchemy import (
    Column, String, Boolean, Integer, Date, Time,
    DateTime, ForeignKey, Text, Enum as SAEnum,
    UniqueConstraint, Index,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import enum

from app.database import Base


# ─────────────────────────────────────────────────────────────
# Enums
# ─────────────────────────────────────────────────────────────

class BookingStatus(str, enum.Enum):
    completed   = "completed"        # applicant finished booking
    auto_completed = "auto_completed"  # interview done (system / HR)
    no_show     = "no_show"          # did not attend (HR manual)
    canceled    = "canceled"         # booking canceled


class SlotStatus(str, enum.Enum):
    open   = "open"
    closed = "closed"


# ─────────────────────────────────────────────────────────────
# Companies  (HR-managed company list)
# ─────────────────────────────────────────────────────────────

class Company(Base):
    __tablename__ = "companies"

    id         = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name       = Column(String(200), nullable=False, unique=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


# ─────────────────────────────────────────────────────────────
# HR Admins
# ─────────────────────────────────────────────────────────────

class HRAdmin(Base):
    __tablename__ = "hr_admins"

    id         = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email      = Column(String(255), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    is_active  = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


# ─────────────────────────────────────────────────────────────
# Positions  (job openings)
# ─────────────────────────────────────────────────────────────

class Position(Base):
    __tablename__ = "positions"

    id        = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title     = Column(String(200), nullable=False, unique=True)
    company   = Column(String(200), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    bookings = relationship("Booking", back_populates="position")


# ─────────────────────────────────────────────────────────────
# Interview Slots
# ─────────────────────────────────────────────────────────────

class InterviewSlot(Base):
    __tablename__ = "interview_slots"

    id           = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    slot_date    = Column(Date, nullable=False)
    start_time   = Column(Time, nullable=False)
    end_time     = Column(Time, nullable=False)
    max_capacity = Column(Integer, nullable=False, default=1)
    booked_count = Column(Integer, nullable=False, default=0)
    status       = Column(SAEnum(SlotStatus), nullable=False, default=SlotStatus.open)
    notes        = Column(Text, nullable=True)
    created_at   = Column(DateTime(timezone=True), server_default=func.now())
    updated_at   = Column(DateTime(timezone=True), onupdate=func.now())

    bookings = relationship("Booking", back_populates="slot")

    __table_args__ = (
        Index("ix_slots_date_status", "slot_date", "status"),
    )


# ─────────────────────────────────────────────────────────────
# Bookings
# ─────────────────────────────────────────────────────────────

class Booking(Base):
    __tablename__ = "bookings"

    id               = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    slot_id          = Column(UUID(as_uuid=True), ForeignKey("interview_slots.id", ondelete="SET NULL"), nullable=True)
    position_id      = Column(UUID(as_uuid=True), ForeignKey("positions.id", ondelete="SET NULL"), nullable=True)

    # Applicant info
    applicant_name   = Column(String(200), nullable=False)
    applicant_email  = Column(String(255), nullable=False, index=True)
    applicant_phone  = Column(String(50), nullable=False)

    # Denormalized slot info (kept in sync; needed for HR edits that detach from slot)
    slot_date        = Column(Date, nullable=True)
    start_time       = Column(Time, nullable=True)
    end_time         = Column(Time, nullable=True)

    # Status
    status           = Column(
        SAEnum(BookingStatus),
        nullable=False,
        default=BookingStatus.completed,
    )

    # Google integrations
    google_meet_link    = Column(String(500), nullable=True)
    google_calendar_event_id = Column(String(500), nullable=True)   # to update/delete calendar event

    booked_at        = Column(DateTime(timezone=True), server_default=func.now())
    updated_at       = Column(DateTime(timezone=True), onupdate=func.now())

    slot     = relationship("InterviewSlot", back_populates="bookings")
    position = relationship("Position", back_populates="bookings")
    history  = relationship(
        "BookingHistory",
        back_populates="booking",
        order_by="BookingHistory.changed_at",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        # One active booking per email (only completed/auto_completed)
        # Enforced at application level; DB uniqueness on email + active statuses
        Index("ix_bookings_email", "applicant_email"),
        Index("ix_bookings_slot_status", "slot_id", "status"),
    )


# ─────────────────────────────────────────────────────────────
# Booking History  (audit trail – one row per HR edit)
# ─────────────────────────────────────────────────────────────

class BookingHistory(Base):
    """
    Immutable snapshot written BEFORE every HR edit.

    Each row captures the full state of the booking *before* the change,
    plus the status it was changed *to*, so you can reconstruct the
    full before/after for any edit.
    """
    __tablename__ = "booking_history"

    id          = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    booking_id  = Column(
        UUID(as_uuid=True),
        ForeignKey("bookings.id", ondelete="CASCADE"),
        nullable=False,
    )

    # ── snapshot of booking state BEFORE this edit ──────────────
    slot_id            = Column(UUID(as_uuid=True), nullable=True)
    position_id        = Column(UUID(as_uuid=True), nullable=True)
    applicant_name     = Column(String(200), nullable=False)
    applicant_email    = Column(String(255), nullable=False)
    applicant_phone    = Column(String(50), nullable=False)
    slot_date          = Column(Date, nullable=True)
    start_time         = Column(Time, nullable=True)
    end_time           = Column(Time, nullable=True)
    status_before      = Column(SAEnum(BookingStatus), nullable=False)
    google_meet_link   = Column(String(500), nullable=True)
    google_calendar_event_id = Column(String(500), nullable=True)

    # ── what triggered this snapshot ────────────────────────────
    status_after  = Column(SAEnum(BookingStatus), nullable=True)   # new status (if changed)
    changed_by    = Column(String(255), nullable=True)             # HR email / identifier
    changed_at    = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    booking = relationship("Booking", back_populates="history")

    __table_args__ = (
        Index("ix_booking_history_booking_id", "booking_id"),
        Index("ix_booking_history_changed_at", "changed_at"),
    )


# ─────────────────────────────────────────────────────────────
# Slot restriction rules (HR-configured)
# ─────────────────────────────────────────────────────────────

class SlotRestrictionRule(Base):
    """
    HR-defined slot visibility rules.

    - One position  → position-exclusive lock (hide slot for same position only)
    - Two+ positions → mutual-exclusion group (hide for all positions in the group)
    """
    __tablename__ = "slot_restriction_rules"

    id         = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    positions = relationship(
        "SlotRestrictionRulePosition",
        back_populates="rule",
        cascade="all, delete-orphan",
    )


class SlotRestrictionRulePosition(Base):
    """Links a position to a restriction rule (same position may appear in multiple rules)."""
    __tablename__ = "slot_restriction_rule_positions"

    id          = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    rule_id     = Column(
        UUID(as_uuid=True),
        ForeignKey("slot_restriction_rules.id", ondelete="CASCADE"),
        nullable=False,
    )
    position_id = Column(
        UUID(as_uuid=True),
        ForeignKey("positions.id", ondelete="CASCADE"),
        nullable=False,
    )

    rule     = relationship("SlotRestrictionRule", back_populates="positions")
    position = relationship("Position")

    __table_args__ = (
        UniqueConstraint("rule_id", "position_id", name="uq_restriction_rule_rule_position"),
        Index("ix_restriction_rule_positions_rule", "rule_id"),
    )