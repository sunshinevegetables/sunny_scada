"""add alarm_rules table

Revision ID: c4a7d1c9e2aa
Revises: merge_3a1e8abb1cda_add_group
Create Date: 2026-02-18

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "c4a7d1c9e2aa"
down_revision = "merge_3a1e8abb1cda_add_group"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "alarm_rules",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("datapoint_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("severity", sa.String(length=30), nullable=False, server_default=sa.text("'info'")),
        sa.Column("comparison", sa.String(length=30), nullable=False, server_default=sa.text("'above'")),
        sa.Column("warning_enabled", sa.Boolean(), nullable=False, server_default=sa.text("0")),

        # One-sided thresholds
        sa.Column("warning_threshold", sa.Float(), nullable=True),
        sa.Column("alarm_threshold", sa.Float(), nullable=True),

        # Range thresholds
        sa.Column("warning_threshold_low", sa.Float(), nullable=True),
        sa.Column("warning_threshold_high", sa.Float(), nullable=True),
        sa.Column("alarm_threshold_low", sa.Float(), nullable=True),
        sa.Column("alarm_threshold_high", sa.Float(), nullable=True),

        sa.Column("schedule_enabled", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("schedule_start_time", sa.Time(), nullable=True),
        sa.Column("schedule_end_time", sa.Time(), nullable=True),
        sa.Column("schedule_timezone", sa.String(length=64), nullable=False, server_default=sa.text("'UTC'")),

        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),

        sa.ForeignKeyConstraint(["datapoint_id"], ["cfg_data_points.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_alarm_rules_datapoint_id"), "alarm_rules", ["datapoint_id"], unique=False)
    op.create_index(op.f("ix_alarm_rules_enabled"), "alarm_rules", ["enabled"], unique=False)
    op.create_index(op.f("ix_alarm_rules_severity"), "alarm_rules", ["severity"], unique=False)
    op.create_index(op.f("ix_alarm_rules_comparison"), "alarm_rules", ["comparison"], unique=False)
    op.create_index("ix_alarm_rules_datapoint_enabled", "alarm_rules", ["datapoint_id", "enabled"], unique=False)

    # Best-effort: ensure the default 'admin' role has alarms:admin (existing installs).
    bind = op.get_bind()
    try:
        row = bind.execute(sa.text("SELECT id FROM roles WHERE name = 'admin'"))
        role = row.fetchone()
        if role:
            role_id = int(role[0])
            existing = bind.execute(
                sa.text(
                    "SELECT permission FROM role_permissions WHERE role_id = :rid AND permission IN ('alarms:admin','alarms:*')"
                ),
                {"rid": role_id},
            ).fetchall()
            existing_perms = {r[0] for r in existing}
            if "alarms:admin" not in existing_perms and "alarms:*" not in existing_perms:
                bind.execute(
                    sa.text("INSERT INTO role_permissions (role_id, permission) VALUES (:rid, 'alarms:admin')"),
                    {"rid": role_id},
                )
    except Exception:
        # Don't fail schema migrations due to optional seed logic.
        pass


def downgrade() -> None:
    bind = op.get_bind()
    try:
        bind.execute(sa.text("DELETE FROM role_permissions WHERE permission = 'alarms:admin'"))
    except Exception:
        pass

    op.drop_index("ix_alarm_rules_datapoint_enabled", table_name="alarm_rules")
    op.drop_index(op.f("ix_alarm_rules_comparison"), table_name="alarm_rules")
    op.drop_index(op.f("ix_alarm_rules_severity"), table_name="alarm_rules")
    op.drop_index(op.f("ix_alarm_rules_enabled"), table_name="alarm_rules")
    op.drop_index(op.f("ix_alarm_rules_datapoint_id"), table_name="alarm_rules")
    op.drop_table("alarm_rules")
