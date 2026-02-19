"""Add hardlink toggle settings and per-tracker hardlink/torrent/qbit fields.

Revision ID: 028_add_hardlink_and_qbit_settings
Revises: 027_add_torrent_output_dir
Create Date: 2026-02-19
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = '028_add_hardlink_and_qbit_settings'
down_revision = '027_add_torrent_output_dir'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- Settings table: global hardlink toggles ---
    with op.batch_alter_table('settings') as batch_op:
        batch_op.add_column(
            sa.Column('hardlink_enabled', sa.Boolean(), nullable=True, server_default=sa.text('1'))
        )
        batch_op.add_column(
            sa.Column('hardlink_fallback_copy', sa.Boolean(), nullable=True, server_default=sa.text('1'))
        )

    # --- Trackers table: per-tracker hardlink/torrent/qbit fields ---
    with op.batch_alter_table('trackers') as batch_op:
        batch_op.add_column(
            sa.Column('hardlink_dir', sa.String(1000), nullable=True)
        )
        batch_op.add_column(
            sa.Column('torrent_dir', sa.String(1000), nullable=True)
        )
        batch_op.add_column(
            sa.Column('inject_to_qbit', sa.Boolean(), nullable=True, server_default=sa.text('1'))
        )


def downgrade() -> None:
    with op.batch_alter_table('trackers') as batch_op:
        batch_op.drop_column('inject_to_qbit')
        batch_op.drop_column('torrent_dir')
        batch_op.drop_column('hardlink_dir')

    with op.batch_alter_table('settings') as batch_op:
        batch_op.drop_column('hardlink_fallback_copy')
        batch_op.drop_column('hardlink_enabled')
