"""Persist the ACME validation method for managed certificates.

Revision ID: 20260924_certificate_challenge
Revises: 20260923_certificate_automation
"""
from alembic import op
import sqlalchemy as sa

revision = "20260924_certificate_challenge"
down_revision = "20260923_certificate_automation"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "certificates",
        sa.Column("challenge", sa.String(20), nullable=False, server_default="dns-01"),
    )


def downgrade():
    op.drop_column("certificates", "challenge")
