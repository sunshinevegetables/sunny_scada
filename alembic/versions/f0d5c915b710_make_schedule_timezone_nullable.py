"""make schedule_timezone nullable

Revision ID: f0d5c915b710
Revises: 6c9d16b54523
Create Date: 2026-02-19 19:29:53.022065

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision = 'f0d5c915b710'
down_revision = '6c9d16b54523'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    insp = inspect(bind)

    if "alarm_rules" not in insp.get_table_names():
        return

    cols = {c["name"]: c for c in insp.get_columns("alarm_rules")}

    # Only modify if column exists and is NOT nullable
    if "schedule_timezone" in cols and cols["schedule_timezone"]["nullable"] is False:
        with op.batch_alter_table("alarm_rules") as batch:
            batch.alter_column(
                "schedule_timezone",
                existing_type=sa.String(length=64),
                nullable=True,
            )


def downgrade():
    # SQLite-safe: no-op
    pass