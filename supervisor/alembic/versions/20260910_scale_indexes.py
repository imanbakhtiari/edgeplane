"""Indexes for large vhost fleets and traffic queries.

Revision ID: 20260910_scale_indexes
Revises: 2e82c5dac817
"""

from alembic import op

revision = "20260910_scale_indexes"
down_revision = "2e82c5dac817"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index("ix_vhosts_active_created", "vhosts", ["deleted_at", "created_at"])
    op.create_index("ix_vhost_domains_vhost", "vhost_domains", ["vhost_id"])
    op.create_index("ix_origins_vhost_position", "origins", ["vhost_id", "position"])
    op.create_index("ix_traffic_buckets_vhost_hour", "traffic_buckets", ["vhost_id", "hour"])
    op.create_index("ix_traffic_buckets_hour_vhost_country", "traffic_buckets", ["hour", "vhost_id", "country"])


def downgrade():
    op.drop_index("ix_traffic_buckets_hour_vhost_country", table_name="traffic_buckets")
    op.drop_index("ix_traffic_buckets_vhost_hour", table_name="traffic_buckets")
    op.drop_index("ix_origins_vhost_position", table_name="origins")
    op.drop_index("ix_vhost_domains_vhost", table_name="vhost_domains")
    op.drop_index("ix_vhosts_active_created", table_name="vhosts")
