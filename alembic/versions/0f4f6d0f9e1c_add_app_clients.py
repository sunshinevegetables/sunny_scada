"""add app_clients table

Revision ID: 0f4f6d0f9e1c
Revises: 7c4b3e2f7f2a
Create Date: 2026-01-12

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0f4f6d0f9e1c"
down_revision = "7c4b3e2f7f2a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "app_clients",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("role_id", sa.Integer(), nullable=True),
        sa.Column("secret_hash", sa.String(length=500), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("token_version", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("allowed_ips", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["role_id"], ["roles.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_index(op.f("ix_app_clients_name"), "app_clients", ["name"], unique=True)
    op.create_index(op.f("ix_app_clients_role_id"), "app_clients", ["role_id"], unique=False)
    op.create_index(op.f("ix_app_clients_is_active"), "app_clients", ["is_active"], unique=False)

    # Backfill new permissions into the default 'admin' role (if present).
    # This avoids breaking existing deployments that created admin roles before these permissions existed.
    conn = op.get_bind()
    role_id = conn.execute(sa.text("SELECT id FROM roles WHERE name = :name"), {"name": "admin"}).scalar()
    if role_id:
        perms = ["plc:read", "plc:write", "iqf:control"]
        dialect = conn.dialect.name
        for p in perms:
            if dialect == "sqlite":
                conn.execute(
                    sa.text("INSERT OR IGNORE INTO role_permissions (role_id, permission) VALUES (:rid, :perm)"),
                    {"rid": int(role_id), "perm": p},
                )
            else:
                conn.execute(
                    sa.text(
                        "INSERT INTO role_permissions (role_id, permission) VALUES (:rid, :perm) ON CONFLICT DO NOTHING"
                    ),
                    {"rid": int(role_id), "perm": p},
                )


def downgrade() -> None:
    op.drop_index(op.f("ix_app_clients_is_active"), table_name="app_clients")
    op.drop_index(op.f("ix_app_clients_role_id"), table_name="app_clients")
    op.drop_index(op.f("ix_app_clients_name"), table_name="app_clients")
    op.drop_table("app_clients")
