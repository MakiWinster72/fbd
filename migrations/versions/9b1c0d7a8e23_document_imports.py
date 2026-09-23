"""Persist document import jobs.

Revision ID: 9b1c0d7a8e23
Revises: 49634040a15c
"""
from alembic import op
import sqlalchemy as sa

revision = "9b1c0d7a8e23"
down_revision = "49634040a15c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "document_imports",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("file_name", sa.String(255), nullable=False),
        sa.Column("knowledge_base_id", sa.String(128), nullable=False),
        sa.Column("file_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("total_chunks", sa.Integer(), nullable=False),
        sa.Column("completed_chunks", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.Column("updated_at", sa.String(40), nullable=False),
    )
    op.create_index("ix_document_imports_knowledge_base_id", "document_imports", ["knowledge_base_id"])


def downgrade() -> None:
    op.drop_index("ix_document_imports_knowledge_base_id", table_name="document_imports")
    op.drop_table("document_imports")
