"""add instrument maintenance schema

Revision ID: d9e1a6b4c3f2
Revises: 8b7d2c4f1e9a
Create Date: 2026-02-21
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "d9e1a6b4c3f2"
down_revision = "8b7d2c4f1e9a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "instruments",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("equipment_id", sa.Integer(), nullable=True),
        sa.Column("vendor_id", sa.Integer(), nullable=True),
        sa.Column("label", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False, server_default=sa.text("'active'")),
        sa.Column("instrument_type", sa.String(length=100), nullable=True),
        sa.Column("model", sa.String(length=120), nullable=True),
        sa.Column("serial_number", sa.String(length=120), nullable=True),
        sa.Column("location", sa.String(length=200), nullable=True),
        sa.Column("installed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("meta", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["equipment_id"], ["equipment.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["vendor_id"], ["vendors.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_instruments_equipment_id"), "instruments", ["equipment_id"], unique=False)
    op.create_index(op.f("ix_instruments_vendor_id"), "instruments", ["vendor_id"], unique=False)
    op.create_index(op.f("ix_instruments_status"), "instruments", ["status"], unique=False)

    op.create_table(
        "instrument_datapoints",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("instrument_id", sa.Integer(), nullable=False),
        sa.Column("cfg_data_point_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=50), nullable=False, server_default=sa.text("'process'")),
        sa.ForeignKeyConstraint(["cfg_data_point_id"], ["cfg_data_points.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["instrument_id"], ["instruments.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("instrument_id", "cfg_data_point_id", "role", name="uq_instrument_datapoints_key"),
    )
    op.create_index(op.f("ix_instrument_datapoints_instrument_id"), "instrument_datapoints", ["instrument_id"], unique=False)
    op.create_index(op.f("ix_instrument_datapoints_cfg_data_point_id"), "instrument_datapoints", ["cfg_data_point_id"], unique=False)

    op.create_table(
        "instrument_calibrations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("instrument_id", sa.Integer(), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("next_due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("method", sa.String(length=120), nullable=True),
        sa.Column("result", sa.String(length=60), nullable=True),
        sa.Column("as_found", sa.Float(), nullable=True),
        sa.Column("as_left", sa.Float(), nullable=True),
        sa.Column("performed_by", sa.String(length=200), nullable=True),
        sa.Column("certificate_no", sa.String(length=120), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("meta", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["instrument_id"], ["instruments.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_instrument_calibrations_instrument_ts",
        "instrument_calibrations",
        ["instrument_id", "ts"],
        unique=False,
    )
    op.create_index(
        "ix_instrument_calibrations_next_due_at",
        "instrument_calibrations",
        ["next_due_at"],
        unique=False,
    )

    op.create_table(
        "instrument_attachments",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("instrument_id", sa.Integer(), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("storage_path", sa.String(length=500), nullable=False),
        sa.Column("content_type", sa.String(length=100), nullable=True),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("meta", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["instrument_id"], ["instruments.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_instrument_attachments_instrument_id"), "instrument_attachments", ["instrument_id"], unique=False)

    op.create_table(
        "instrument_spare_map",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("instrument_id", sa.Integer(), nullable=False),
        sa.Column("part_id", sa.Integer(), nullable=False),
        sa.Column("qty_required", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("meta", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["instrument_id"], ["instruments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["part_id"], ["spare_parts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("instrument_id", "part_id", name="uq_instrument_spare_map_instrument_part"),
    )
    op.create_index(op.f("ix_instrument_spare_map_instrument_id"), "instrument_spare_map", ["instrument_id"], unique=False)
    op.create_index(op.f("ix_instrument_spare_map_part_id"), "instrument_spare_map", ["part_id"], unique=False)

    with op.batch_alter_table("work_orders") as batch:
        batch.add_column(sa.Column("instrument_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_work_orders_instrument_id",
            "instruments",
            ["instrument_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_index(op.f("ix_work_orders_instrument_id"), ["instrument_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("work_orders") as batch:
        batch.drop_index(op.f("ix_work_orders_instrument_id"))
        batch.drop_constraint("fk_work_orders_instrument_id", type_="foreignkey")
        batch.drop_column("instrument_id")

    op.drop_index(op.f("ix_instrument_spare_map_part_id"), table_name="instrument_spare_map")
    op.drop_index(op.f("ix_instrument_spare_map_instrument_id"), table_name="instrument_spare_map")
    op.drop_table("instrument_spare_map")

    op.drop_index(op.f("ix_instrument_attachments_instrument_id"), table_name="instrument_attachments")
    op.drop_table("instrument_attachments")

    op.drop_index("ix_instrument_calibrations_next_due_at", table_name="instrument_calibrations")
    op.drop_index("ix_instrument_calibrations_instrument_ts", table_name="instrument_calibrations")
    op.drop_table("instrument_calibrations")

    op.drop_index(op.f("ix_instrument_datapoints_cfg_data_point_id"), table_name="instrument_datapoints")
    op.drop_index(op.f("ix_instrument_datapoints_instrument_id"), table_name="instrument_datapoints")
    op.drop_table("instrument_datapoints")

    op.drop_index(op.f("ix_instruments_status"), table_name="instruments")
    op.drop_index(op.f("ix_instruments_vendor_id"), table_name="instruments")
    op.drop_index(op.f("ix_instruments_equipment_id"), table_name="instruments")
    op.drop_table("instruments")
