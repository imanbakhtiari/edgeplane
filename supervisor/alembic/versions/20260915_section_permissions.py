"""Add per-user application section permissions.

Revision ID: 20260915_section_permissions
Revises: 20260910_scale_indexes
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260915_section_permissions"
down_revision = "20260910_scale_indexes"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "users",
        sa.Column("section_permissions", postgresql.JSONB(), server_default="[]", nullable=False),
    )


def downgrade():
    op.drop_column("users", "section_permissions")
