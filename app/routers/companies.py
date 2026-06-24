"""
app/routers/companies.py  –  Company CRUD (HR only)
"""
from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.models.models import Company
from app.schemas.schemas import CompanyCreate, CompanyOut
from app.services.auth import get_current_hr

router = APIRouter(prefix="/api/companies", tags=["Companies"])


@router.get("", response_model=List[CompanyOut])
async def list_companies(db: AsyncSession = Depends(get_db)):
    """Public – applicant booking page needs this to show company list."""
    result = await db.execute(select(Company).order_by(Company.name))
    return result.scalars().all()


@router.post("", response_model=CompanyOut)
async def create_company(
    body: CompanyCreate,
    db: AsyncSession = Depends(get_db),
    _hr: str = Depends(get_current_hr),
):
    # 防止重複
    existing = await db.execute(select(Company).where(Company.name == body.name.strip()))
    if existing.scalar_one_or_none():
        raise HTTPException(400, "公司名稱已存在")
    company = Company(name=body.name.strip())
    db.add(company)
    await db.commit()
    await db.refresh(company)
    return company


@router.delete("/{company_id}")
async def delete_company(
    company_id: UUID,
    db: AsyncSession = Depends(get_db),
    _hr: str = Depends(get_current_hr),
):
    result = await db.execute(select(Company).where(Company.id == company_id))
    company = result.scalar_one_or_none()
    if not company:
        raise HTTPException(404, "公司不存在")
    await db.delete(company)
    await db.commit()
    return {"message": "已刪除"}
