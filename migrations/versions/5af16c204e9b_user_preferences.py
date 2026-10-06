"""Store each user's display-language preference.

Revision ID: 5af16c204e9b
Revises: 13c4e65d2baf
"""
import sqlalchemy as sa
from alembic import op

revision = "5af16c204e9b"
down_revision = "13c4e65d2baf"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_preferences",
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("preferred_locale", sa.String(5), nullable=False),
        sa.Column("updated_at", sa.String(40), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("user_preferences")
