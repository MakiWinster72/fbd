"""Store account-owned conversations and atomic question/answer turns.

Revision ID: 6d8c09a31e7f
Revises: 5af16c204e9b
"""
import sqlalchemy as sa
from alembic import op

revision = "6d8c09a31e7f"
down_revision = "5af16c204e9b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "conversations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("knowledge_base_id", sa.String(128), sa.ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(100), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.Column("updated_at", sa.String(40), nullable=False),
    )
    for column in ("user_id", "knowledge_base_id", "updated_at"):
        op.create_index(f"ix_conversations_{column}", "conversations", [column])
    op.create_table(
        "conversation_turns",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("conversation_id", sa.String(36), sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("question", sa.Text, nullable=False),
        sa.Column("answer", sa.Text, nullable=False),
        sa.Column("grounded", sa.Boolean, nullable=False),
        sa.Column("locale", sa.String(5), nullable=False),
        sa.Column("sources_json", sa.Text, nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
    )
    op.create_index("ix_conversation_turns_conversation_id", "conversation_turns", ["conversation_id"])


def downgrade() -> None:
    op.drop_table("conversation_turns")
    op.drop_table("conversations")
