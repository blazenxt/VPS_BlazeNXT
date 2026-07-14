"""Add Web Push preferences and subscriptions.

Revision ID: 0002_web_push
Revises: 0001_blazenxt_v1
"""
from alembic import op
import sqlalchemy as sa
revision='0002_web_push'
down_revision='0001_blazenxt_v1'
branch_labels=None
depends_on=None
def upgrade():
    bind=op.get_bind();inspector=sa.inspect(bind);tables=set(inspector.get_table_names())
    columns={x['name'] for x in inspector.get_columns('notification_preferences')}
    if 'push_enabled' not in columns:op.add_column('notification_preferences',sa.Column('push_enabled',sa.Boolean(),nullable=False,server_default=sa.true()))
    if 'push_subscriptions' not in tables:
        op.create_table('push_subscriptions',sa.Column('id',sa.Integer(),primary_key=True),sa.Column('user_id',sa.Integer(),sa.ForeignKey('users.id',ondelete='CASCADE'),nullable=False),sa.Column('endpoint',sa.Text(),nullable=False,unique=True),sa.Column('p256dh',sa.Text(),nullable=False),sa.Column('auth',sa.Text(),nullable=False),sa.Column('user_agent',sa.String(length=300)),sa.Column('enabled',sa.Boolean(),nullable=False,server_default=sa.true()),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),sa.Column('last_success_at',sa.DateTime(timezone=True)),sa.Column('last_error',sa.Text()));op.create_index('ix_push_subscriptions_user_id','push_subscriptions',['user_id']);op.create_index('ix_push_subscriptions_enabled','push_subscriptions',['enabled'])
def downgrade():
    bind=op.get_bind();inspector=sa.inspect(bind);tables=set(inspector.get_table_names())
    if 'push_subscriptions' in tables:op.drop_index('ix_push_subscriptions_enabled',table_name='push_subscriptions');op.drop_index('ix_push_subscriptions_user_id',table_name='push_subscriptions');op.drop_table('push_subscriptions')
    columns={x['name'] for x in sa.inspect(bind).get_columns('notification_preferences')}
    if 'push_enabled' in columns:op.drop_column('notification_preferences','push_enabled')
