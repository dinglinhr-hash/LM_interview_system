"""
app/routers/restriction_rules.py  –  HR slot restriction rule CRUD
"""
from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.models import Position, SlotRestrictionRule, SlotRestrictionRulePosition
from app.schemas.schemas import (
    RestrictionRuleCreate,
    RestrictionRuleOut,
    RestrictionRulePositionOut,
    RestrictionRuleUpdate,
)

router = APIRouter(prefix="/api/restriction-rules", tags=["Restriction Rules"])


def _rule_type_label(position_count: int) -> tuple[str, str]:
    if position_count <= 1:
        return (
            "position_exclusive",
            "時段獨立限制 (限 1 人)",
        )
    return (
        "group_exclusive",
        "時段共用限制",
    )


def _build_display_label(positions: list) -> str:
    titles = [p.position.title for p in positions if p.position]
    if not titles:
        return "（無職務）"
    rule_type, suffix = _rule_type_label(len(titles))
    if rule_type == "position_exclusive":
        return f"{titles[0]} {suffix}"
    return f"{' ＋ '.join(titles)} {suffix}"


def _rule_to_out(rule: SlotRestrictionRule) -> RestrictionRuleOut:
    positions = sorted(rule.positions, key=lambda rp: (rp.position.title if rp.position else ""))
    rule_type, _ = _rule_type_label(len(positions))
    return RestrictionRuleOut(
        id=rule.id,
        rule_type=rule_type,
        display_label=_build_display_label(rule.positions),
        positions=[
            RestrictionRulePositionOut(
                position_id=rp.position_id,
                position_title=rp.position.title if rp.position else "",
            )
            for rp in positions
        ],
        created_at=rule.created_at,
        updated_at=rule.updated_at,
    )


async def _validate_position_ids(db: AsyncSession, position_ids: List[UUID]) -> List[Position]:
    result = await db.execute(select(Position).where(Position.id.in_(position_ids)))
    found = {p.id: p for p in result.scalars().all()}
    missing = [str(pid) for pid in position_ids if pid not in found]
    if missing:
        raise HTTPException(400, f"找不到職務：{', '.join(missing)}")
    return [found[pid] for pid in position_ids]


@router.get("", response_model=List[RestrictionRuleOut])
async def list_restriction_rules(
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(SlotRestrictionRule)
        .options(
            selectinload(SlotRestrictionRule.positions).selectinload(
                SlotRestrictionRulePosition.position
            )
        )
        .order_by(SlotRestrictionRule.created_at.desc())
    )
    return [_rule_to_out(r) for r in result.scalars().all()]


@router.post("", response_model=RestrictionRuleOut)
async def create_restriction_rule(
    body: RestrictionRuleCreate,
    db: AsyncSession = Depends(get_db),
):
    await _validate_position_ids(db, body.position_ids)

    rule = SlotRestrictionRule()
    db.add(rule)
    await db.flush()

    for pid in body.position_ids:
        db.add(SlotRestrictionRulePosition(rule_id=rule.id, position_id=pid))

    await db.commit()

    result = await db.execute(
        select(SlotRestrictionRule)
        .options(
            selectinload(SlotRestrictionRule.positions).selectinload(
                SlotRestrictionRulePosition.position
            )
        )
        .where(SlotRestrictionRule.id == rule.id)
    )
    rule = result.scalar_one()
    return _rule_to_out(rule)


@router.patch("/{rule_id}", response_model=RestrictionRuleOut)
async def update_restriction_rule(
    rule_id: UUID,
    body: RestrictionRuleUpdate,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(SlotRestrictionRule)
        .options(selectinload(SlotRestrictionRule.positions))
        .where(SlotRestrictionRule.id == rule_id)
    )
    rule = result.scalar_one_or_none()
    if not rule:
        raise HTTPException(404, "規則不存在")

    await _validate_position_ids(db, body.position_ids)

    for rp in list(rule.positions):
        await db.delete(rp)
    await db.flush()

    for pid in body.position_ids:
        db.add(SlotRestrictionRulePosition(rule_id=rule.id, position_id=pid))

    await db.commit()

    result = await db.execute(
        select(SlotRestrictionRule)
        .options(
            selectinload(SlotRestrictionRule.positions).selectinload(
                SlotRestrictionRulePosition.position
            )
        )
        .where(SlotRestrictionRule.id == rule_id)
    )
    rule = result.scalar_one()
    return _rule_to_out(rule)


@router.delete("/{rule_id}")
async def delete_restriction_rule(
    rule_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(SlotRestrictionRule).where(SlotRestrictionRule.id == rule_id)
    )
    rule = result.scalar_one_or_none()
    if not rule:
        raise HTTPException(404, "規則不存在")

    await db.delete(rule)
    await db.commit()
    return {"message": "Restriction rule deleted"}