"""
main.py  –  FastAPI application entry point
"""
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.database import engine, Base
from app.config import settings

# Routers
from app.routers import (
    hr_auth,
    applicant_auth,
    positions,
    companies,
    slots,
    bookings,
    pages,
    calendar,
    restriction_rules,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# Startup: create tables + seed HR admin
# ─────────────────────────────────────────────────────────────

async def _migrate_restriction_rule_constraints(conn):
    """Allow the same position in multiple restriction rules (PostgreSQL)."""
    from sqlalchemy import text

    if not str(engine.url).startswith("postgresql"):
        return

    await conn.execute(
        text(
            "ALTER TABLE slot_restriction_rule_positions "
            "DROP CONSTRAINT IF EXISTS uq_restriction_rule_position"
        )
    )
    await conn.execute(
        text(
            """
            DO $$ BEGIN
                IF EXISTS (
                    SELECT 1 FROM information_schema.tables
                    WHERE table_schema = 'public'
                      AND table_name = 'slot_restriction_rule_positions'
                ) AND NOT EXISTS (
                    SELECT 1 FROM pg_constraint
                    WHERE conname = 'uq_restriction_rule_rule_position'
                ) THEN
                    ALTER TABLE slot_restriction_rule_positions
                    ADD CONSTRAINT uq_restriction_rule_rule_position
                    UNIQUE (rule_id, position_id);
                END IF;
            END $$;
            """
        )
    )


async def _seed_hr_admin():
    from sqlalchemy import select
    from app.database import AsyncSessionLocal
    from app.models.models import HRAdmin
    from app.services.auth import hash_password

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(HRAdmin).where(HRAdmin.email == settings.HR_ADMIN_EMAIL)
        )
        if not result.scalar_one_or_none():
            admin = HRAdmin(
                email=settings.HR_ADMIN_EMAIL,
                password_hash=hash_password(settings.HR_ADMIN_PASSWORD),
            )
            db.add(admin)
            await db.commit()
            logger.info("Seeded HR admin: %s", settings.HR_ADMIN_EMAIL)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create all tables (idempotent – won't drop existing data)
    async with engine.begin() as conn:
        # Import models so Base.metadata knows about them
        from app.models import models  # noqa: F401
        await conn.run_sync(Base.metadata.create_all)
        await _migrate_restriction_rule_constraints(conn)

    # Seed HR admin
    await _seed_hr_admin()

    # Initialize calendar database
    from app.services import google_calendar_manager
    google_calendar_manager.init_db()

    # Check and update expired bookings on startup
    from app.services.booking_status_checker import start_booking_status_checker
    await start_booking_status_checker()

    from app.database import AsyncSessionLocal
    from app.services.slot_capacity import repair_slots_state

    async with AsyncSessionLocal() as db:
        synced, reopened = await repair_slots_state(db)
        if synced or reopened:
            logger.info(
                "Slot repair: synced booked_count on %s slot(s), reopened %s",
                synced,
                reopened,
            )

    logger.info("Application started")
    yield
    await engine.dispose()
    logger.info("Application shutdown")


# ─────────────────────────────────────────────────────────────
# App instance
# ─────────────────────────────────────────────────────────────

app = FastAPI(
    title="Interview Platform API",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# ─── Routers ─────────────────────────────────────────────────
app.include_router(hr_auth.router)
app.include_router(applicant_auth.router)
app.include_router(positions.router)
app.include_router(companies.router)
app.include_router(slots.router)
app.include_router(bookings.router)
app.include_router(pages.router)
app.include_router(calendar.router)
app.include_router(restriction_rules.router)

# Alias: /api/booking-edit-options  →  same handler as /api/bookings/edit-options
# (used by hr_bookings.html which calls /api/booking-edit-options)
from app.routers.bookings import get_booking_edit_options, cancel_interview #AppsScript 串接
from app.database import get_db
from app.services.auth import get_current_hr
from fastapi import Depends

@app.get("/api/booking-edit-options", tags=["Bookings"])
async def booking_edit_options_alias(
    db=Depends(get_db),
    _hr: str = Depends(get_current_hr),
):
    return await get_booking_edit_options(db=db, _hr=_hr)


# Alias: /apps_script  →  /api/bookings/apps_script
# (for external interview cancellation requests)
from app.schemas.schemas import InterviewPayload

@app.post("/apps_script", tags=["Interview"])
async def apps_script_endpoint(
    payload: InterviewPayload,
    db=Depends(get_db),
):
    return await cancel_interview(payload=payload, db=db)



if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=5000,
        reload=False,
    )

