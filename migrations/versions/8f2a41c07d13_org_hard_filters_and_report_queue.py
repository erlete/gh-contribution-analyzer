"""org hard filters and report queue

Revision ID: 8f2a41c07d13
Revises: cd35edd96629
Create Date: 2026-08-05 11:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "8f2a41c07d13"
down_revision: str | None = "cd35edd96629"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "orgs",
        sa.Column(
            "ignore_forks", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.add_column(
        "orgs",
        sa.Column(
            "members_only", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.create_table(
        "org_members",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("login", sa.String(length=200), nullable=False),
        sa.Column("login_norm", sa.String(length=200), nullable=False),
        sa.Column("node_id", sa.String(length=100), nullable=True),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["orgs.id"],
            name=op.f("fk_org_members_org_id_orgs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_org_members")),
        sa.UniqueConstraint(
            "org_id", "login_norm", name=op.f("uq_org_members_org_id")
        ),
    )
    op.create_index(
        op.f("ix_org_members_org_id"), "org_members", ["org_id"], unique=False
    )
    op.add_column(
        "reports",
        sa.Column(
            "params",
            sa.JSON().with_variant(
                postgresql.JSONB(astext_type=sa.Text()), "postgresql"
            ),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("reports", "params")
    op.drop_index(op.f("ix_org_members_org_id"), table_name="org_members")
    op.drop_table("org_members")
    op.drop_column("orgs", "members_only")
    op.drop_column("orgs", "ignore_forks")
