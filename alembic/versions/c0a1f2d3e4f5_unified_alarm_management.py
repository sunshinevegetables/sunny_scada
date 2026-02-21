"""unified alarm management tables

Revision ID: c0a1f2d3e4f5
Revises: merge_3a1e8abb1cda_add_group
Create Date: 2026-02-19 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision = "c0a1f2d3e4f5"
down_revision = "merge_3a1e8abb1cda_add_group"
branch_labels = None
depends_on = None


def _index_exists(insp, table_name: str, index_name: str) -> bool:
    try:
        for idx in insp.get_indexes(table_name):
            if idx.get("name") == index_name:
                return True
    except Exception:
        return False
    return False


def upgrade():
    bind = op.get_bind()
    insp = inspect(bind)
    existing_tables = set(insp.get_table_names())

    # ---- alarm_rules: create if missing, otherwise ALTER to new shape ----
    if "alarm_rules" not in existing_tables:
        op.create_table(
            "alarm_rules",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("datapoint_id", sa.Integer(), sa.ForeignKey("cfg_data_points.id", ondelete="CASCADE"), nullable=False),
            sa.Column("rule_source", sa.String(length=20), nullable=False, server_default="backend"),
            sa.Column("external_rule_id", sa.String(length=200), nullable=True),
            sa.Column("name", sa.String(length=200), nullable=False),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("1")),
            sa.Column("severity", sa.String(length=30), nullable=False, server_default="info"),
            sa.Column("comparison", sa.String(length=30), nullable=False, server_default="above"),
            sa.Column("warning_enabled", sa.Boolean(), nullable=False, server_default=sa.text("0")),
            sa.Column("warning_threshold", sa.Float(), nullable=True),
            sa.Column("alarm_threshold", sa.Float(), nullable=True),
            sa.Column("warning_threshold_low", sa.Float(), nullable=True),
            sa.Column("warning_threshold_high", sa.Float(), nullable=True),
            sa.Column("alarm_threshold_low", sa.Float(), nullable=True),
            sa.Column("alarm_threshold_high", sa.Float(), nullable=True),
            sa.Column("schedule_enabled", sa.Boolean(), nullable=False, server_default=sa.text("0")),
            sa.Column("schedule_start_time", sa.Time(), nullable=True),
            sa.Column("schedule_end_time", sa.Time(), nullable=True),
            sa.Column("schedule_timezone", sa.String(length=64), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
            sa.CheckConstraint("rule_source IN ('backend','frontend')", name="ck_alarm_rule_source"),
            sa.CheckConstraint(
                "comparison IN ('above','below','outside_range','inside_range')",
                name="ck_alarm_rule_comparison",
            ),
        )
    else:
        # Table exists from older migration -> add missing columns needed by newer code.
        cols = {c["name"] for c in insp.get_columns("alarm_rules")}

        with op.batch_alter_table("alarm_rules") as batch:
            if "rule_source" not in cols:
                batch.add_column(sa.Column("rule_source", sa.String(length=20), nullable=False, server_default="backend"))
            if "external_rule_id" not in cols:
                batch.add_column(sa.Column("external_rule_id", sa.String(length=200), nullable=True))

        # Refresh inspector after ALTER
        insp = inspect(op.get_bind())

    # Ensure datapoint_id index exists (avoid duplicates)
    if not _index_exists(insp, "alarm_rules", "ix_alarm_rules_dp"):
        op.create_index("ix_alarm_rules_dp", "alarm_rules", ["datapoint_id"], unique=False)

    # ---- unified tables ----
    op.create_table(
        "alarm_occurrences",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("source", sa.String(length=40), nullable=False),
        sa.Column("key", sa.String(length=500), nullable=False),
        sa.Column("datapoint_id", sa.Integer(), sa.ForeignKey("cfg_data_points.id", ondelete="SET NULL"), nullable=True),
        sa.Column("rule_id", sa.Integer(), sa.ForeignKey("alarm_rules.id", ondelete="SET NULL"), nullable=True),
        sa.Column("external_rule_id", sa.String(length=200), nullable=True),
        sa.Column("state", sa.String(length=20), nullable=False, server_default="OK"),
        sa.Column("severity", sa.String(length=30), nullable=False, server_default="info"),
        sa.Column("message", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("value", sa.Float(), nullable=True),
        sa.Column("warning_threshold", sa.Float(), nullable=True),
        sa.Column("alarm_threshold", sa.Float(), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("cleared_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("acknowledged", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("acknowledged_by_client_ip", sa.String(length=64), nullable=True),
        sa.Column("meta", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.UniqueConstraint("source", "key", name="uq_alarm_occ_source_key"),
        sa.CheckConstraint("state IN ('OK','WARNING','ALARM')", name="ck_alarm_occ_state"),
    )
    op.create_index("ix_alarm_occ_src_state", "alarm_occurrences", ["source", "state"], unique=False)
    op.create_index("ix_alarm_occ_src_key", "alarm_occurrences", ["source", "key"], unique=False)
    op.create_index("ix_alarm_occ_dp", "alarm_occurrences", ["datapoint_id"], unique=False)
    op.create_index("ix_alarm_occ_rule", "alarm_occurrences", ["rule_id"], unique=False)

    op.create_table(
        "alarm_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("occurrence_id", sa.Integer(), sa.ForeignKey("alarm_occurrences.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("source", sa.String(length=40), nullable=False),
        sa.Column("key", sa.String(length=500), nullable=False),
        sa.Column("datapoint_id", sa.Integer(), sa.ForeignKey("cfg_data_points.id", ondelete="SET NULL"), nullable=True),
        sa.Column("rule_id", sa.Integer(), sa.ForeignKey("alarm_rules.id", ondelete="SET NULL"), nullable=True),
        sa.Column("external_rule_id", sa.String(length=200), nullable=True),
        sa.Column("prev_state", sa.String(length=20), nullable=True),
        sa.Column("new_state", sa.String(length=20), nullable=False),
        sa.Column("severity", sa.String(length=30), nullable=False, server_default="info"),
        sa.Column("message", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("value", sa.Float(), nullable=True),
        sa.Column("meta", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.CheckConstraint("new_state IN ('OK','WARNING','ALARM')", name="ck_alarm_evt_new_state"),
    )
    op.create_index("ix_alarm_evt_src_ts", "alarm_events", ["source", "ts"], unique=False)
    op.create_index("ix_alarm_evt_occ_ts", "alarm_events", ["occurrence_id", "ts"], unique=False)


def downgrade():
    op.drop_index("ix_alarm_evt_occ_ts", table_name="alarm_events")
    op.drop_index("ix_alarm_evt_src_ts", table_name="alarm_events")
    op.drop_table("alarm_events")

    op.drop_index("ix_alarm_occ_rule", table_name="alarm_occurrences")
    op.drop_index("ix_alarm_occ_dp", table_name="alarm_occurrences")
    op.drop_index("ix_alarm_occ_src_key", table_name="alarm_occurrences")
    op.drop_index("ix_alarm_occ_src_state", table_name="alarm_occurrences")
    op.drop_table("alarm_occurrences")

    # keep conservative: if alarm_rules existed previously, downgrade shouldn't drop it.
    # But for consistency with original migration intent, only drop if present:
    bind = op.get_bind()
    insp = inspect(bind)
    if "alarm_rules" in set(insp.get_table_names()):
        # This may drop a shared table in legacy installs; use with caution.
        try:
            op.drop_index("ix_alarm_rules_dp", table_name="alarm_rules")
        except Exception:
            pass
        op.drop_table("alarm_rules")
