"""Add booking_history table for HR edit audit trail

Revision ID: a1b2c3d4e5f6
Revises: 4e3132f79bae
Create Date: 2026-06-09 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '4e3132f79bae'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'booking_history',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            'booking_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('bookings.id', ondelete='CASCADE'),
            nullable=False,
        ),

        # ── snapshot of booking state BEFORE this edit ──────────
        sa.Column('slot_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('position_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('applicant_name', sa.String(200), nullable=False),
        sa.Column('applicant_email', sa.String(255), nullable=False),
        sa.Column('applicant_phone', sa.String(50), nullable=False),
        sa.Column('slot_date', sa.Date(), nullable=True),
        sa.Column('start_time', sa.Time(), nullable=True),
        sa.Column('end_time', sa.Time(), nullable=True),
        sa.Column(
            'status_before',
            sa.Enum(
                'completed', 'auto_completed', 'no_show', 'canceled',
                name='bookingstatus',
                create_type=False,   # reuse the enum already defined on bookings
            ),
            nullable=False,
        ),
        sa.Column('google_meet_link', sa.String(500), nullable=True),
        sa.Column('google_calendar_event_id', sa.String(500), nullable=True),

        # ── what triggered this snapshot ────────────────────────
        sa.Column(
            'status_after',
            sa.Enum(
                'completed', 'auto_completed', 'no_show', 'canceled',
                name='bookingstatus',
                create_type=False,
            ),
            nullable=True,
        ),
        sa.Column('changed_by', sa.String(255), nullable=True),
        sa.Column(
            'changed_at',
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    op.create_index('ix_booking_history_booking_id', 'booking_history', ['booking_id'])
    op.create_index('ix_booking_history_changed_at', 'booking_history', ['changed_at'])


def downgrade() -> None:
    op.drop_index('ix_booking_history_changed_at', table_name='booking_history')
    op.drop_index('ix_booking_history_booking_id', table_name='booking_history')
    op.drop_table('booking_history')
