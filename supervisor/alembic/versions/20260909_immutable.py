"""Enforce append-only revision and audit history in PostgreSQL."""

from alembic import op

revision = "20260909_immutable"
down_revision = "6934dbaab042"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE FUNCTION cdn_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN RAISE EXCEPTION 'Immutable CDN history cannot be updated or deleted'; END $$""")
    for table in ["configuration_revisions", "configuration_revision_items", "audit_logs", "job_events"]:
        op.execute(
            f"CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION cdn_immutable()"
        )


def downgrade():
    for table in ["configuration_revisions", "configuration_revision_items", "audit_logs", "job_events"]:
        op.execute(f"DROP TRIGGER immutable_history ON {table}")
    op.execute("DROP FUNCTION cdn_immutable()")
