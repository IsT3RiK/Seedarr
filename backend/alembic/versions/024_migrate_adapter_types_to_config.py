"""Migrate legacy adapter_type values to 'config'

Revision ID: 024_migrate_adapter_types
Revises: 023_add_wizard_completed
Create Date: 2026-02-06 12:00:00.000000

This migration updates all trackers with legacy adapter_type values ('lacale', 'c411')
to use 'config' instead. The ConfigAdapter now handles all trackers via YAML configs.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '024_migrate_adapter_types'
down_revision = '023_add_wizard_completed'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Migrate legacy adapter_type values to 'config'."""
    # Update lacale and c411 adapter types to config
    op.execute(
        "UPDATE trackers SET adapter_type = 'config' "
        "WHERE adapter_type IN ('lacale', 'c411')"
    )


def downgrade() -> None:
    """No safe downgrade - adapter types were already migrated."""
    pass
