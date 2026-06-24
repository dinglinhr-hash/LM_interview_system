"""
app/schemas/schemas.py  –  All Pydantic request / response models
"""
from __future__ import annotations
from uuid import UUID
from datetime import date, time, datetime
from typing import Optional, List
from pydantic import BaseModel, EmailStr, field_validator, model_validator
from app.models.models import BookingStatus, SlotStatus


# ─────────────────────────────────────────────────────────────
# Auth
# ─────────────────────────────────────────────────────────────

class HRLoginResponse(BaseModel):
    ok: bool
    access_token: str
    token_type: str = "bearer"


class HRUpdateCredentials(BaseModel):
    new_email: Optional[str] = None
    new_password: Optional[str] = None
    current_password: str  # 必須驗證舊密碼才能修改


# ─────────────────────────────────────────────────────────────
# HR Admin Management  (admin-level CRUD for all HR accounts)
# ─────────────────────────────────────────────────────────────

class HRAdminOut(BaseModel):
    id: UUID
    email: str
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class HRAdminCreate(BaseModel):
    email: EmailStr
    password: str


class HRAdminUpdate(BaseModel):
    email: Optional[EmailStr] = None
    password: Optional[str] = None
    is_active: Optional[bool] = None


# ─────────────────────────────────────────────────────────────
# Company
# ─────────────────────────────────────────────────────────────

class CompanyCreate(BaseModel):
    name: str


class CompanyOut(BaseModel):
    id: UUID
    name: str
    created_at: datetime

    model_config = {"from_attributes": True}


# ─────────────────────────────────────────────────────────────
# Position
# ─────────────────────────────────────────────────────────────

class PositionCreate(BaseModel):
    title: str
    company: Optional[str] = None


class PositionUpdate(BaseModel):
    title: Optional[str] = None
    is_active: Optional[bool] = None
    company: Optional[str] = None


class PositionOut(BaseModel):
    id: UUID
    title: str
    company: Optional[str] = None
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


# ─────────────────────────────────────────────────────────────
# Interview Slot
# ─────────────────────────────────────────────────────────────

class SlotCreate(BaseModel):
    slot_date: date
    start_time: str    # "HH:MM"
    end_time: str      # "HH:MM"
    max_capacity: int = 1
    notes: Optional[str] = None


class SlotUpdate(BaseModel):
    slot_date: Optional[date] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    max_capacity: Optional[int] = None
    notes: Optional[str] = None
    status: Optional[SlotStatus] = None


class SlotOut(BaseModel):
    id: UUID
    slot_date: date
    start_time: str
    end_time: str
    max_capacity: int
    booked_count: int
    status: SlotStatus
    notes: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}

    @field_validator("start_time", "end_time", mode="before")
    @classmethod
    def time_to_str(cls, v):
        if isinstance(v, time):
            return v.strftime("%H:%M:%S")
        return v


class SlotBookingSummary(BaseModel):
    """Minimal booking info shown in slot booking preview panel."""
    applicant_name: str
    position_title: Optional[str] = None

    model_config = {"from_attributes": True}


# ─────────────────────────────────────────────────────────────
# Booking – applicant-facing
# ─────────────────────────────────────────────────────────────

class BookingCreate(BaseModel):
    slot_id: UUID
    position_id: UUID
    name: str
    email: EmailStr
    phone: str


class BookingModify(BaseModel):
    """Applicant modifies their own booking."""
    booking_id: UUID
    slot_id: UUID
    position_id: UUID
    name: str
    email: EmailStr
    phone: str


# ─────────────────────────────────────────────────────────────
# Booking – HR-facing PATCH
# ─────────────────────────────────────────────────────────────

class BookingHRUpdate(BaseModel):
    applicant_name: Optional[str] = None
    applicant_email: Optional[EmailStr] = None
    applicant_phone: Optional[str] = None
    position_id: Optional[UUID] = None
    slot_date: Optional[date] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    google_meet_link: Optional[str] = None
    status: Optional[BookingStatus] = None


# ─────────────────────────────────────────────────────────────
# Booking – response
# ─────────────────────────────────────────────────────────────

class BookingOut(BaseModel):
    id: UUID
    slot_id: Optional[UUID] = None
    position_id: Optional[UUID] = None
    position_title: Optional[str] = None

    applicant_name: str
    applicant_email: str
    applicant_phone: str

    slot_date: Optional[date] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None

    status: BookingStatus
    google_meet_link: Optional[str] = None
    google_calendar_event_id: Optional[str] = None

    booked_at: datetime
    updated_at: Optional[datetime] = None

    model_config = {"from_attributes": True}

    @field_validator("start_time", "end_time", mode="before")
    @classmethod
    def time_to_str(cls, v):
        if isinstance(v, time):
            return v.strftime("%H:%M:%S")
        return v


class BookingByEmailOut(BaseModel):
    booking_id: UUID
    slot_id: Optional[UUID] = None
    position_id: Optional[UUID] = None
    slot_date: Optional[date] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    position_title: Optional[str] = None
    applicant_name: Optional[str] = None
    applicant_phone: Optional[str] = None

    model_config = {"from_attributes": True}

    @field_validator("start_time", "end_time", mode="before")
    @classmethod
    def time_to_str(cls, v):
        if isinstance(v, time):
            return v.strftime("%H:%M:%S")
        return v


# ─────────────────────────────────────────────────────────────
# Booking History – HR audit trail
# ─────────────────────────────────────────────────────────────

class BookingHistoryOut(BaseModel):
    """One audit-trail entry: the state of a booking BEFORE an HR edit."""
    id: UUID
    booking_id: UUID

    slot_id: Optional[UUID] = None
    position_id: Optional[UUID] = None
    applicant_name: str
    applicant_email: str
    applicant_phone: str
    slot_date: Optional[date] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    status_before: BookingStatus
    status_after: Optional[BookingStatus] = None
    google_meet_link: Optional[str] = None
    google_calendar_event_id: Optional[str] = None

    changed_by: Optional[str] = None
    changed_at: datetime

    model_config = {"from_attributes": True}

    @field_validator("start_time", "end_time", mode="before")
    @classmethod
    def time_to_str(cls, v):
        if isinstance(v, time):
            return v.strftime("%H:%M:%S")
        return v


# ─────────────────────────────────────────────────────────────
# Booking edit options (for HR dropdown)
# ─────────────────────────────────────────────────────────────

class BookingEditOptions(BaseModel):
    positions: List[PositionOut]
    slots: List[SlotOut]


# ─────────────────────────────────────────────────────────────
# Interview Cancellation Payload AppsScript 串接
# ─────────────────────────────────────────────────────────────

class InterviewPayload(BaseModel):
    """Payload for interview cancellation endpoint."""
    submit_time: str
    email: EmailStr
    title: str
    canceled_result: str
    others_result: str


# ─────────────────────────────────────────────────────────────
# Slot restriction rules (HR)
# ─────────────────────────────────────────────────────────────

class RestrictionRulePositionIn(BaseModel):
    position_id: UUID


class RestrictionRuleCreate(BaseModel):
    position_ids: List[UUID]

    @field_validator("position_ids")
    @classmethod
    def at_least_one(cls, v: List[UUID]) -> List[UUID]:
        if not v:
            raise ValueError("至少需要選擇一個職務")
        if len(v) != len(set(v)):
            raise ValueError("職務不可重複選擇")
        return v


class RestrictionRuleUpdate(BaseModel):
    position_ids: List[UUID]

    @field_validator("position_ids")
    @classmethod
    def at_least_one(cls, v: List[UUID]) -> List[UUID]:
        if not v:
            raise ValueError("至少需要選擇一個職務")
        if len(v) != len(set(v)):
            raise ValueError("職務不可重複選擇")
        return v


class RestrictionRulePositionOut(BaseModel):
    position_id: UUID
    position_title: str

    model_config = {"from_attributes": True}


class RestrictionRuleOut(BaseModel):
    id: UUID
    rule_type: str  # "position_exclusive" | "group_exclusive"
    display_label: str
    positions: List[RestrictionRulePositionOut]
    created_at: datetime
    updated_at: Optional[datetime] = None

    model_config = {"from_attributes": True}