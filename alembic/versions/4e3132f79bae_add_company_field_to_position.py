"""Add company field to Position

Revision ID: 4e3132f79bae
Revises: 
Create Date: 2026-06-02 16:58:06.465709

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text


revision: str = '4e3132f79bae'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    # 用 IF NOT EXISTS 避免欄位已存在時報錯
    op.execute(text("""
        ALTER TABLE positions
        ADD COLUMN IF NOT EXISTS company VARCHAR(200)
    """))

def downgrade() -> None:
    op.drop_column('positions', 'company')