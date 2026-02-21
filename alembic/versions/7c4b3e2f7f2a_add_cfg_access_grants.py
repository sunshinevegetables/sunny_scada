"""add cfg_access_grants table for RBAC

Revision ID: 7c4b3e2f7f2a
Revises: 3a2d8f6bbd10
Create Date: 2026-01-11

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "7c4b3e2f7f2a"
down_revision = "3a2d8f6bbd10"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cfg_access_grants",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("role_id", sa.Integer(), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("resource_type", sa.String(length=30), nullable=False),
        sa.Column("resource_id", sa.Integer(), nullable=False),
        sa.Column("access_level", sa.String(length=10), nullable=False, server_default=sa.text("'read'")),
        sa.Column("include_descendants", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["role_id"], ["roles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "(role_id IS NOT NULL AND user_id IS NULL) OR (role_id IS NULL AND user_id IS NOT NULL)",
            name="ck_cfg_access_grants_principal",
        ),
        sa.CheckConstraint(
            "resource_type IN ('plc','container','equipment','datapoint')",
            name="ck_cfg_access_grants_resource_type",
        ),
        sa.CheckConstraint(
            "access_level IN ('read','write')",
            name="ck_cfg_access_grants_access_level",
        ),
        sa.UniqueConstraint("role_id", "resource_type", "resource_id", name="uq_cfg_access_grants_role_resource"),
        sa.UniqueConstraint("user_id", "resource_type", "resource_id", name="uq_cfg_access_grants_user_resource"),
    )

    op.create_index(op.f("ix_cfg_access_grants_role_id"), "cfg_access_grants", ["role_id"], unique=False)
    op.create_index(op.f("ix_cfg_access_grants_user_id"), "cfg_access_grants", ["user_id"], unique=False)
    op.create_index(
        "ix_cfg_access_grants_resource",
        "cfg_access_grants",
        ["resource_type", "resource_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_cfg_access_grants_resource", table_name="cfg_access_grants")
    op.drop_index(op.f("ix_cfg_access_grants_user_id"), table_name="cfg_access_grants")
    op.drop_index(op.f("ix_cfg_access_grants_role_id"), table_name="cfg_access_grants")
    op.drop_table("cfg_access_grants")
