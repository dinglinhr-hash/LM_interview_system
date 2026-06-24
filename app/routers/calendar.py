"""
app/routers/calendar.py
─────────────────────────────────────────────────────────────
Calendar management API endpoints
"""
import logging
from typing import Optional, List
from datetime import datetime

from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, EmailStr

from app.services import google_calendar_manager as calendar_service
from app.services.auth import get_current_hr

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/calendar", tags=["calendar"])


# ─────────────────────────────────────────────
# Request/Response Models
# ─────────────────────────────────────────────
class EventCreateRequest(BaseModel):
    """Create event request"""
    summary: str
    start: str  # "YYYY-MM-DD HH:MM"
    end: str
    location: Optional[str] = ""
    description: Optional[str] = ""
    add_meet: Optional[bool] = False
    attendees: Optional[List[EmailStr]] = None
    send_ical: Optional[bool] = True


class EventUpdateRequest(BaseModel):
    """Update event request"""
    summary: Optional[str] = None
    start: Optional[str] = None  # "YYYY-MM-DD HH:MM"
    end: Optional[str] = None
    location: Optional[str] = None
    description: Optional[str] = None
    add_meet: Optional[bool] = None
    attendees: Optional[List[EmailStr]] = None
    send_ical: Optional[bool] = True


class EventResponse(BaseModel):
    """Event response"""
    id: str
    summary: str
    start: dict
    end: dict
    location: Optional[str] = None
    description: Optional[str] = None
    attendees: Optional[list] = None
    conferenceData: Optional[dict] = None
    meet_link: Optional[str] = None

    class Config:
        from_attributes = True


class EventListResponse(BaseModel):
    """Event list response"""
    events: List[dict]
    count: int


class MessageResponse(BaseModel):
    """Generic message response"""
    message: str
    success: bool


# ─────────────────────────────────────────────
# Initialization
# ─────────────────────────────────────────────
@router.post("/init", response_model=MessageResponse, dependencies=[Depends(get_current_hr)])
async def initialize_calendar():
    """Initialize calendar database"""
    try:
        calendar_service.init_db()
        return MessageResponse(message="Calendar database initialized", success=True)
    except Exception as e:
        logger.error(f"Failed to initialize calendar: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ─────────────────────────────────────────────
# Event Listing
# ─────────────────────────────────────────────
@router.get("/events", response_model=EventListResponse, dependencies=[Depends(get_current_hr)])
async def list_events(
    max_results: int = Query(10, ge=1, le=100),
    days_ahead: int = Query(30, ge=1, le=365),
    query: Optional[str] = None,
):
    """List upcoming events"""
    try:
        events = calendar_service.list_events(
            max_results=max_results,
            days_ahead=days_ahead,
            query=query
        )
        return EventListResponse(events=events, count=len(events))
    except Exception as e:
        logger.error(f"Failed to list events: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/events/saved", dependencies=[Depends(get_current_hr)])
async def list_saved_events():
    """List all saved event IDs"""
    try:
        events = calendar_service.list_saved_events()
        return {
            "events": [
                {
                    "summary": e[0],
                    "event_id": e[1],
                    "created_at": e[2]
                }
                for e in events
            ],
            "count": len(events)
        }
    except Exception as e:
        logger.error(f"Failed to list saved events: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ─────────────────────────────────────────────
# Event Queries
# ─────────────────────────────────────────────
@router.get("/events/{event_id}", response_model=dict, dependencies=[Depends(get_current_hr)])
async def get_event(event_id: str):
    """Get event details"""
    try:
        event = calendar_service.get_event(event_id)
        if not event:
            raise HTTPException(status_code=404, detail="Event not found")
        return event
    except Exception as e:
        logger.error(f"Failed to get event: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/events/by-summary/{summary}", dependencies=[Depends(get_current_hr)])
async def get_event_by_summary(summary: str):
    """Get event ID by summary"""
    try:
        event_id = calendar_service.load_event_id(summary)
        if not event_id:
            raise HTTPException(status_code=404, detail=f"Event '{summary}' not found")
        return {"summary": summary, "event_id": event_id}
    except Exception as e:
        logger.error(f"Failed to load event ID: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ─────────────────────────────────────────────
# Event Creation
# ─────────────────────────────────────────────
@router.post("/events", response_model=dict, dependencies=[Depends(get_current_hr)])
async def create_event(request: EventCreateRequest):
    """Create new event with optional Google Meet and iCal invitation"""
    try:
        # Validate date format
        try:
            datetime.strptime(request.start, "%Y-%m-%d %H:%M")
            datetime.strptime(request.end, "%Y-%m-%d %H:%M")
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail="Invalid date format. Use 'YYYY-MM-DD HH:MM'"
            )

        event = calendar_service.create_event(
            summary=request.summary,
            start=request.start,
            end=request.end,
            location=request.location or "",
            description=request.description or "",
            add_meet=request.add_meet or False,
            attendees=request.attendees or [],
            send_ical=request.send_ical if request.attendees else False,
        )

        if not event:
            raise HTTPException(status_code=500, detail="Failed to create event")

        # Extract meet link if available
        meet_link = (
            event.get("conferenceData", {})
            .get("entryPoints", [{}])[0]
            .get("uri", "")
        )

        return {
            "success": True,
            "event_id": event.get("id"),
            "summary": event.get("summary"),
            "meet_link": meet_link,
            "event": event
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to create event: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ─────────────────────────────────────────────
# Event Updates
# ─────────────────────────────────────────────
@router.patch("/events/{event_id}", response_model=dict, dependencies=[Depends(get_current_hr)])
async def update_event(event_id: str, request: EventUpdateRequest):
    """Update event"""
    try:
        # Validate date format if provided
        if request.start:
            try:
                datetime.strptime(request.start, "%Y-%m-%d %H:%M")
            except ValueError:
                raise HTTPException(
                    status_code=400,
                    detail="Invalid start date format. Use 'YYYY-MM-DD HH:MM'"
                )
        if request.end:
            try:
                datetime.strptime(request.end, "%Y-%m-%d %H:%M")
            except ValueError:
                raise HTTPException(
                    status_code=400,
                    detail="Invalid end date format. Use 'YYYY-MM-DD HH:MM'"
                )

        event = calendar_service.update_event(
            event_id=event_id,
            summary=request.summary,
            start=request.start,
            end=request.end,
            location=request.location,
            description=request.description,
            add_meet=request.add_meet,
            attendees=request.attendees,
            send_ical=request.send_ical,
        )

        if not event:
            raise HTTPException(status_code=500, detail="Failed to update event")

        # Extract meet link if available
        meet_link = (
            event.get("conferenceData", {})
            .get("entryPoints", [{}])[0]
            .get("uri", "")
        )

        return {
            "success": True,
            "event_id": event.get("id"),
            "summary": event.get("summary"),
            "meet_link": meet_link,
            "event": event
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update event: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ─────────────────────────────────────────────
# Event Deletion
# ─────────────────────────────────────────────
@router.delete("/events/{event_id}", response_model=MessageResponse, dependencies=[Depends(get_current_hr)])
async def delete_event(
    event_id: str,
    send_ical: bool = Query(True),
):
    """Delete event and send cancellation notice"""
    try:
        success = calendar_service.delete_event(
            event_id=event_id,
            send_ical=send_ical
        )

        if not success:
            raise HTTPException(status_code=500, detail="Failed to delete event")

        return MessageResponse(message="Event deleted successfully", success=True)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete event: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ─────────────────────────────────────────────
# Event Records
# ─────────────────────────────────────────────
@router.delete("/events/records/{summary}", response_model=MessageResponse, dependencies=[Depends(get_current_hr)])
async def delete_event_record(summary: str):
    """Soft-delete event record from database"""
    try:
        calendar_service.delete_event_record(summary)
        return MessageResponse(message="Event record deleted", success=True)
    except Exception as e:
        logger.error(f"Failed to delete event record: {e}")
        raise HTTPException(status_code=500, detail=str(e))
