"""
app/routers/positions.py  –  Job position CRUD
"""
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.models.models import Position
from app.schemas.schemas import PositionCreate, PositionUpdate, PositionOut
from app.services.auth import get_current_hr

router = APIRouter(prefix="/api/positions", tags=["Positions"])


@router.get("", response_model=List[PositionOut])
async def list_positions(
    active_only: bool = True,
    company: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """Public endpoint – used by applicant booking page."""
    q = select(Position).order_by(Position.title)
    if active_only:
        q = q.where(Position.is_active == True)
    if company:
        q = q.where(Position.company == company)
    result = await db.execute(q)
    return result.scalars().all()


@router.post("", response_model=PositionOut)
async def create_position(
    body: PositionCreate,
    db: AsyncSession = Depends(get_db),
    _hr: str = Depends(get_current_hr),
):
    pos = Position(title=body.title.strip(), company=body.company)
    db.add(pos)
    await db.commit()
    await db.refresh(pos)
    return pos


@router.patch("/{position_id}", response_model=PositionOut)
async def update_position(
    position_id: UUID,
    body: PositionUpdate,
    db: AsyncSession = Depends(get_db),
    _hr: str = Depends(get_current_hr),
):
    result = await db.execute(select(Position).where(Position.id == position_id))
    pos = result.scalar_one_or_none()
    if not pos:
        raise HTTPException(404, "Position not found")

    if body.title is not None:
        pos.title = body.title.strip()
    if body.is_active is not None:
        pos.is_active = body.is_active
    if body.company is not None:
        pos.company = body.company

    await db.commit()
    await db.refresh(pos)
    return pos


@router.patch("/{position_id}/visibility", response_model=PositionOut)
async def toggle_position_visibility(
    position_id: UUID,
    db: AsyncSession = Depends(get_db),
    _hr: str = Depends(get_current_hr),
):
    result = await db.execute(select(Position).where(Position.id == position_id))
    pos = result.scalar_one_or_none()
    if not pos:
        raise HTTPException(404, "Position not found")

    pos.is_active = not pos.is_active
    await db.commit()
    await db.refresh(pos)
    return pos


@router.delete("/{position_id}")
async def delete_position(
    position_id: UUID,
    db: AsyncSession = Depends(get_db),
    _hr: str = Depends(get_current_hr),
):
    result = await db.execute(select(Position).where(Position.id == position_id))
    pos = result.scalar_one_or_none()
    if not pos:
        raise HTTPException(404, "Position not found")

    await db.delete(pos)
    await db.commit()
    return {"message": "Position deleted"}
