"""Persist node onboarding and connection activity.

Revision ID: 20260915_agent_activities
Revises: 20260915_section_permissions
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260915_agent_activities"
down_revision = "20260915_section_permissions"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "agent_activities",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("agent_nodes.id"), nullable=False),
        sa.Column("stage", sa.String(50), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index("ix_agent_activities_agent_id", "agent_activities", ["agent_id"])


def downgrade():
    op.drop_table("agent_activities")
