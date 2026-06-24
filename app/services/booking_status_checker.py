"""
app/services/booking_status_checker.py  –  Auto-complete booking when interview time has passed
"""
import asyncio
import logging
from datetime import datetime, timezone, date as dt_date, time as dt_time
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from sqlalchemy.orm import joinedload

from app.database import AsyncSessionLocal
from app.models.models import Booking, BookingStatus, InterviewSlot
from app.services import email_service

logger = logging.getLogger(__name__)


async def check_and_update_expired_bookings(db: AsyncSession = None):
    """
    檢查所有預約，如果面試時間已過期（當前時間 > 預約結束時間），
    則自動將狀態從 completed 轉為 auto_completed。
    
    This function should be called periodically (e.g., on startup or via a background task).
    """
    if db is None:
        db = AsyncSessionLocal()
    
    try:
        # Get current date and time
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        current_date = now.date()
        current_time = now.time()
        
        logger.info(f"Checking for expired bookings at {now}")
        
        # Find all bookings with status "completed" that have passed their end time
        result = await db.execute(
            select(Booking)
            .options(joinedload(Booking.position))
            .where(
                and_(
                    Booking.status == BookingStatus.completed,
                    Booking.slot_date.isnot(None),
                    Booking.end_time.isnot(None),
                    # Interview has ended: 
                    # (slot_date < today) OR (slot_date == today AND end_time < current_time)
                    (
                        (Booking.slot_date < current_date) |
                        (
                            (Booking.slot_date == current_date) & 
                            (Booking.end_time <= current_time)
                        )
                    )
                )
            )
        )
        
        expired_bookings = result.scalars().all()
        
        if expired_bookings:
            logger.info(f"Found {len(expired_bookings)} expired bookings to update")
            
            old_statuses = {}
            for booking in expired_bookings:
                old_statuses[booking.id] = booking.status
                booking.status = BookingStatus.auto_completed
                logger.info(
                    f"Updated booking {booking.id} for {booking.applicant_email}: "
                    f"{old_statuses[booking.id]} → {booking.status} "
                    f"(Interview ended at {booking.slot_date} {booking.end_time})"
                )

            await db.commit()
            logger.info(f"Successfully updated {len(expired_bookings)} bookings to auto_completed")

            # ── Notify HR for each auto_completed booking ──────────────
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
                        new_status=booking.status.value,
                        old_status=old_statuses[booking.id].value,
                    )
                )
        else:
            logger.info("No expired bookings found")
        
        return len(expired_bookings)
    
    except Exception as e:
        logger.error(f"Error checking expired bookings: {str(e)}", exc_info=True)
        await db.rollback()
        return 0
    finally:
        if db != AsyncSessionLocal():
            await db.close()


async def start_booking_status_checker():
    """
    啟動預約狀態檢查器的入口點。
    在應用啟動時調用此函數。
    """
    logger.info("Starting booking status checker...")
    
    db = AsyncSessionLocal()
    try:
        updated_count = await check_and_update_expired_bookings(db)
        logger.info(f"Booking status checker completed: {updated_count} bookings updated")
    except Exception as e:
        logger.error(f"Failed to start booking status checker: {str(e)}", exc_info=True)
    finally:
        await db.close()
