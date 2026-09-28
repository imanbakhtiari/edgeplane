"""Certificate source and automatic renewal metadata.

Revision ID: 20260923_certificate_automation
Revises: 20260915_agent_activities
"""
from alembic import op
import sqlalchemy as sa

revision = "20260923_certificate_automation"
down_revision = "20260915_agent_activities"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("certificates", sa.Column("source", sa.String(20), nullable=False, server_default="manual"))
    op.add_column("certificates", sa.Column("auto_renew", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("certificates", sa.Column("renew_before_days", sa.Integer(), nullable=False, server_default="30"))
    op.add_column("certificates", sa.Column("email", sa.String(320), nullable=True))
    op.add_column("certificates", sa.Column("status", sa.String(30), nullable=False, server_default="READY"))


def downgrade():
    for name in ("status", "email", "renew_before_days", "auto_renew", "source"):
        op.drop_column("certificates", name)
