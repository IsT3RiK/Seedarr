"""Add notification settings and logs table

Revision ID: 013_add_notifications
Revises: 012_add_processing_queue
Create Date: 2026-01-24 21:00:00.000000

This migration adds:
- Notification settings to settings table
- notification_logs table for tracking sent notifications
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '013_add_notifications'
down_revision = '012_add_processing_queue'
branch_labels = None
depends_on = None


def column_exists(connection, table_name, column_name):
    """Check if a column exists in a table."""
    inspector = sa.inspect(connection)
    if table_name not in inspector.get_table_names():
        return False
    columns = [col['name'] for col in inspector.get_columns(table_name)]
    return column_name in columns


def table_exists(connection, table_name):
    """Check if a table exists."""
    inspector = sa.inspect(connection)
    return table_name in inspector.get_table_names()


def upgrade() -> None:
    """Add notification settings and logs table."""
    connection = op.get_bind()

    # Add notification settings columns to settings table
    notification_columns = [
        ('discord_webhook_url', sa.String(500)),
        ('notification_email', sa.String(255)),
        ('smtp_host', sa.String(255)),
        ('smtp_port', sa.Integer()),
        ('smtp_username', sa.String(255)),
        ('smtp_password', sa.String(500)),
        ('smtp_from', sa.String(255)),
        ('smtp_use_tls', sa.Integer()),
    ]

    for col_name, col_type in notification_columns:
        if not column_exists(connection, 'settings', col_name):
            op.add_column('settings', sa.Column(col_name, col_type, nullable=True))

    # Create notification_logs table
    if not table_exists(connection, 'notification_logs'):
        op.create_table(
            'notification_logs',
            sa.Column('id', sa.Integer(), nullable=False, primary_key=True, autoincrement=True),
            sa.Column('channel', sa.String(50), nullable=False),
            sa.Column('event', sa.String(50), nullable=False),
            sa.Column('recipient', sa.String(500), nullable=True),
            sa.Column('subject', sa.String(500), nullable=True),
            sa.Column('message', sa.Text(), nullable=True),
            sa.Column('success', sa.Boolean(), nullable=False, default=False),
            sa.Column('error_message', sa.String(1000), nullable=True),
            sa.Column('file_entry_id', sa.Integer(), nullable=True),
            sa.Column('batch_id', sa.Integer(), nullable=True),
            sa.Column('metadata', sa.JSON(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False),
        )


def downgrade() -> None:
    """Remove notification settings and logs table."""
    connection = op.get_bind()

    # Drop notification_logs table
    if table_exists(connection, 'notification_logs'):
        op.drop_table('notification_logs')

    # Remove notification columns from settings
    notification_columns = [
        'discord_webhook_url', 'notification_email', 'smtp_host', 'smtp_port',
        'smtp_username', 'smtp_password', 'smtp_from', 'smtp_use_tls'
    ]

    for col_name in notification_columns:
        if column_exists(connection, 'settings', col_name):
            op.drop_column('settings', col_name)
