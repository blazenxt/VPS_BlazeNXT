"""BlazeNXT v1.0.0 schema baseline.

Revision ID: 0001_blazenxt_v1
Revises: None
"""
from alembic import op
from app.db import Base
import app.models  # noqa: F401
revision='0001_blazenxt_v1'
down_revision=None
branch_labels=None
depends_on=None
def upgrade():
    # The baseline intentionally uses the canonical SQLAlchemy metadata so a new
    # installation and an existing create_all installation converge exactly.
    Base.metadata.create_all(bind=op.get_bind())
def downgrade():
    # Destructive baseline downgrade is intentionally disabled to protect data.
    pass
