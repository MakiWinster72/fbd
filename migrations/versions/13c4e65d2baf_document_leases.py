"""Coordinate document operations across application workers.

Revision ID: 13c4e65d2baf
Revises: 9b1c0d7a8e23
"""
from alembic import op
import sqlalchemy as sa

revision = "13c4e65d2baf"
down_revision = "9b1c0d7a8e23"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "document_leases",
        sa.Column("knowledge_base_id", sa.String(128), primary_key=True),
        sa.Column("document_id", sa.String(64), primary_key=True),
        sa.Column("owner", sa.String(36), nullable=False),
        sa.Column("job_id", sa.String(36)),
        sa.Column("expires_at", sa.String(40), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("document_leases")
