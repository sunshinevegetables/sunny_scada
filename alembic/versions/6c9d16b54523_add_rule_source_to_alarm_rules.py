"""add rule_source to alarm_rules

Revision ID: 6c9d16b54523
Revises: 055f24ab3325
Create Date: 2026-02-19 19:25:18.611504

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision = '6c9d16b54523'
down_revision = '055f24ab3325'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    insp = inspect(bind)

    if "alarm_rules" not in set(insp.get_table_names()):
        return

    cols = {c["name"] for c in insp.get_columns("alarm_rules")}

    # SQLite-safe alter
    with op.batch_alter_table("alarm_rules") as batch:
        if "rule_source" not in cols:
            batch.add_column(
                sa.Column("rule_source", sa.String(20), nullable=False, server_default="backend")
            )
        if "external_rule_id" not in cols:
            batch.add_column(
                sa.Column("external_rule_id", sa.String(200), nullable=True)
            )


def downgrade():
    # SQLite doesn't support DROP COLUMN easily; keep as no-op
    pass