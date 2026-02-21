"""add datapoint meta fields (class/unit/group/multiplier)

Revision ID: 9d8b4c2a1f3e
Revises: 0f4f6d0f9e1c
Create Date: 2026-01-15

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "9d8b4c2a1f3e"
down_revision = "0f4f6d0f9e1c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Option tables for datapoint metadata
    op.create_table(
        "cfg_data_point_classes",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
        sa.Column("updated_by_user_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_cfg_data_point_classes_name"),
    )
    op.create_index(op.f("ix_cfg_data_point_classes_name"), "cfg_data_point_classes", ["name"], unique=True)

    op.create_table(
        "cfg_data_point_units",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
        sa.Column("updated_by_user_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_cfg_data_point_units_name"),
    )
    op.create_index(op.f("ix_cfg_data_point_units_name"), "cfg_data_point_units", ["name"], unique=True)

    op.create_table(
        "cfg_data_point_groups",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
        sa.Column("updated_by_user_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_cfg_data_point_groups_name"),
    )
    op.create_index(op.f("ix_cfg_data_point_groups_name"), "cfg_data_point_groups", ["name"], unique=True)

    # Extend cfg_data_points with meta references + multiplier
    with op.batch_alter_table("cfg_data_points") as batch_op:
        batch_op.add_column(sa.Column("group_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("class_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("unit_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("multiplier", sa.Integer(), nullable=False, server_default=sa.text("1")))

        batch_op.create_foreign_key(
            "fk_cfg_data_points_group_id",
            "cfg_data_point_groups",
            ["group_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch_op.create_foreign_key(
            "fk_cfg_data_points_class_id",
            "cfg_data_point_classes",
            ["class_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch_op.create_foreign_key(
            "fk_cfg_data_points_unit_id",
            "cfg_data_point_units",
            ["unit_id"],
            ["id"],
            ondelete="RESTRICT",
        )

    op.create_index(op.f("ix_cfg_data_points_group_id"), "cfg_data_points", ["group_id"], unique=False)
    op.create_index(op.f("ix_cfg_data_points_class_id"), "cfg_data_points", ["class_id"], unique=False)
    op.create_index(op.f("ix_cfg_data_points_unit_id"), "cfg_data_points", ["unit_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_cfg_data_points_unit_id"), table_name="cfg_data_points")
    op.drop_index(op.f("ix_cfg_data_points_class_id"), table_name="cfg_data_points")
    op.drop_index(op.f("ix_cfg_data_points_group_id"), table_name="cfg_data_points")

    with op.batch_alter_table("cfg_data_points") as batch_op:
        batch_op.drop_constraint("fk_cfg_data_points_unit_id", type_="foreignkey")
        batch_op.drop_constraint("fk_cfg_data_points_class_id", type_="foreignkey")
        batch_op.drop_constraint("fk_cfg_data_points_group_id", type_="foreignkey")

        batch_op.drop_column("multiplier")
        batch_op.drop_column("unit_id")
        batch_op.drop_column("class_id")
        batch_op.drop_column("group_id")

    op.drop_index(op.f("ix_cfg_data_point_groups_name"), table_name="cfg_data_point_groups")
    op.drop_table("cfg_data_point_groups")

    op.drop_index(op.f("ix_cfg_data_point_units_name"), table_name="cfg_data_point_units")
    op.drop_table("cfg_data_point_units")

    op.drop_index(op.f("ix_cfg_data_point_classes_name"), table_name="cfg_data_point_classes")
    op.drop_table("cfg_data_point_classes")
