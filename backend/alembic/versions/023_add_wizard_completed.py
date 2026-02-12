"""Add wizard_completed field to settings

Revision ID: 023_add_wizard_completed
Revises: 022_add_naming_and_nfo_templates
Create Date: 2026-01-29 12:00:00.000000

This migration adds:
- wizard_completed column to settings table for tracking setup wizard completion state

The wizard_completed field is used by the first-time setup wizard to determine
whether to show the wizard or redirect to the dashboard.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '023_add_wizard_completed'
down_revision = '022_add_naming_and_nfo_templates'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add wizard_completed column to settings table."""
    # Add wizard_completed column
    op.add_column(
        'settings',
        sa.Column('wizard_completed', sa.Boolean(), nullable=True, default=False)
    )


def downgrade() -> None:
    """Remove wizard_completed column from settings table."""
    op.drop_column('settings', 'wizard_completed')
