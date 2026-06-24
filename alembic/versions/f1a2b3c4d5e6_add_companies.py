"""add companies table

Revision ID: f1a2b3c4d5e6
Revises: a1b2c3d4e5f6
Create Date: 2026-06-11

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID
import uuid

# revision identifiers
revision = 'f1a2b3c4d5e6'
down_revision = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'companies',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column('name', sa.String(200), nullable=False, unique=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    # 預先插入現有兩家公司，方便遷移後不用手動新增
    op.execute("""
        INSERT INTO companies (id, name)
        VALUES
            (gen_random_uuid(), '台灣骨庫股份有限公司'),
            (gen_random_uuid(), '鼎霖醫療器材股份有限公司')
        ON CONFLICT (name) DO NOTHING;
    """)


def downgrade() -> None:
    op.drop_table('companies')
