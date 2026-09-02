"""initial schema

Revision ID: 001
Revises:
Create Date: 2026-05-21

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '001'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute('CREATE EXTENSION IF NOT EXISTS pgcrypto')
    op.execute('CREATE EXTENSION IF NOT EXISTS btree_gist')

    # Create tenants table
    op.create_table(
        'tenants',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('subscription_plan', sa.String(50), server_default='starter'),
        sa.Column('status', sa.String(50), server_default='active'),
        sa.Column('max_agents', sa.Integer, server_default='5'),
        sa.Column('max_leads_per_month', sa.Integer, server_default='1000'),
        sa.Column('settings', postgresql.JSONB, server_default='{}'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now()),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    )

    # Create users table
    op.create_table(
        'users',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('tenant_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('tenants.id'), nullable=False, index=True),
        sa.Column('email', sa.String(255), unique=True, nullable=False),
        sa.Column('password_hash', sa.String(255), nullable=False),
        sa.Column('first_name', sa.String(100), nullable=False),
        sa.Column('last_name', sa.String(100), nullable=False),
        sa.Column('role', sa.String(50), nullable=False, server_default='agent'),
        sa.Column('status', sa.String(50), nullable=False, server_default='active'),
        sa.Column('last_login_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('failed_login_attempts', sa.Integer, server_default='0'),
        sa.Column('locked_until', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now()),
        sa.Column('preferences', postgresql.JSONB, server_default='{}'),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    )

    # Create agents table
    op.create_table(
        'agents',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('tenant_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('tenants.id'), nullable=False, index=True),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id'), unique=True, nullable=False),
        sa.Column('timezone', sa.String(50), server_default='America/New_York'),
        sa.Column('daily_capacity', sa.Integer, server_default='8'),
        sa.Column('max_concurrent', sa.Integer, server_default='1'),
        sa.Column('skills', postgresql.JSONB, server_default='[]'),
        sa.Column('weight', sa.Integer, server_default='100'),
        sa.Column('status', sa.String(50), server_default='active'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now()),
    )

    # Create campaigns table
    op.create_table(
        'campaigns',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('tenant_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('tenants.id'), nullable=False, index=True),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('description', sa.Text, nullable=True),
        sa.Column('status', sa.String(50), server_default='draft'),
        sa.Column('tone', sa.String(50), server_default='friendly'),
        sa.Column('prompt_template', sa.Text, nullable=True),
        sa.Column('objection_prompts', postgresql.JSONB, server_default='{}'),
        sa.Column('max_retries', sa.Integer, server_default='3'),
        sa.Column('retry_delay_hours', sa.Integer, server_default='24'),
        sa.Column('retry_tones', postgresql.JSONB, server_default='[]'),
        sa.Column('booking_enabled', sa.Boolean, server_default='true'),
        sa.Column('slot_duration_minutes', sa.Integer, server_default='15'),
        sa.Column('max_days_ahead', sa.Integer, server_default='3'),
        sa.Column('business_hours_start', sa.Integer, server_default='10'),
        sa.Column('business_hours_end', sa.Integer, server_default='21'),
        sa.Column('target_sources', postgresql.JSONB, server_default='[]'),
        sa.Column('target_states', postgresql.JSONB, server_default='[]'),
        sa.Column('min_lead_score', sa.Float, nullable=True),
        sa.Column('max_lead_score', sa.Float, nullable=True),
        sa.Column('total_leads', sa.Integer, server_default='0'),
        sa.Column('total_contacted', sa.Integer, server_default='0'),
        sa.Column('total_replied', sa.Integer, server_default='0'),
        sa.Column('total_booked', sa.Integer, server_default='0'),
        sa.Column('total_completed', sa.Integer, server_default='0'),
        sa.Column('total_won', sa.Integer, server_default='0'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now()),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    )

    # Create leads table
    op.create_table(
        'leads',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('tenant_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('tenants.id'), nullable=False),
        sa.Column('source', sa.String(255), nullable=False),
        sa.Column('source_metadata', postgresql.JSONB, server_default='{}'),
        sa.Column('first_name', sa.String(255), nullable=False),
        sa.Column('last_name', sa.String(255), nullable=False),
        sa.Column('phone', sa.String(50), nullable=False),
        sa.Column('phone_normalized', sa.String(20)),
        sa.Column('email', sa.String(255)),
        sa.Column('email_normalized', sa.String(255)),
        sa.Column('state', sa.String(50)),
        sa.Column('city', sa.String(100)),
        sa.Column('zip_code', sa.String(20)),
        sa.Column('timezone', sa.String(50)),
        sa.Column('lead_score', sa.Float, server_default='0'),
        sa.Column('booking_probability', sa.Float, server_default='0'),
        sa.Column('conversion_probability', sa.Float, server_default='0'),
        sa.Column('lifecycle_stage', sa.String(50), nullable=False, server_default='new'),
        sa.Column('ai_status', sa.String(50), nullable=True),
        sa.Column('status', sa.String(50), nullable=False, server_default='new'),
        sa.Column('campaign_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('campaigns.id'), nullable=True),
        sa.Column('contact_count', sa.Integer, server_default='0'),
        sa.Column('last_contacted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_replied_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('first_response_time_seconds', sa.Integer, nullable=True),
        sa.Column('tags', postgresql.JSONB, server_default='[]'),
        sa.Column('custom_fields', postgresql.JSONB, server_default='{}'),
        sa.Column('notes', sa.Text, nullable=True),
        sa.Column('sms_consent', sa.Boolean, server_default='false'),
        sa.Column('email_consent', sa.Boolean, server_default='false'),
        sa.Column('consent_updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now()),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_by', postgresql.UUID(as_uuid=True), nullable=True),
        sa.CheckConstraint('lead_score >= 0 AND lead_score <= 100', name='ck_leads_score_range'),
        sa.CheckConstraint('booking_probability >= 0 AND booking_probability <= 100', name='ck_leads_booking_prob_range'),
        sa.CheckConstraint('conversion_probability >= 0 AND conversion_probability <= 100', name='ck_leads_conversion_prob_range'),
    )
    op.create_index('idx_leads_tenant_id', 'leads', ['tenant_id'])
    op.create_index('idx_leads_tenant_status', 'leads', ['tenant_id', 'status'])
    op.create_index('idx_leads_phone', 'leads', ['tenant_id', 'phone_normalized'])
    op.create_index('idx_leads_email', 'leads', ['tenant_id', 'email_normalized'])
    op.create_index('idx_leads_score', 'leads', ['tenant_id', 'lead_score'])
    op.create_index('idx_leads_campaign', 'leads', ['tenant_id', 'campaign_id'])
    op.create_index('idx_leads_created', 'leads', ['tenant_id', 'created_at'])
    op.create_index('idx_leads_lifecycle', 'leads', ['tenant_id', 'lifecycle_stage'])
    op.create_index('idx_leads_active', 'leads', ['tenant_id', 'deleted_at'])

    # Create conversations table
    op.create_table(
        'conversations',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('tenant_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('tenants.id'), nullable=False),
        sa.Column('lead_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('leads.id'), nullable=False),
        sa.Column('status', sa.String(50), server_default='initiated'),
        sa.Column('intent', sa.String(50), nullable=True),
        sa.Column('sentiment', sa.String(50), nullable=True),
        sa.Column('message_count', sa.Integer, server_default='0'),
        sa.Column('last_message_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_message_from', sa.String(20), nullable=True),
        sa.Column('ai_context', postgresql.JSONB, server_default='{}'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now()),
    )
    op.create_index('idx_conversations_tenant_lead', 'conversations', ['tenant_id', 'lead_id'])
    op.create_index('idx_conversations_status', 'conversations', ['tenant_id', 'status'])
    op.create_index('idx_conversations_last_message', 'conversations', ['tenant_id', 'last_message_at'])

    # Create messages table
    op.create_table(
        'messages',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('conversation_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('conversations.id'), nullable=False),
        sa.Column('tenant_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('tenants.id'), nullable=False),
        sa.Column('sender', sa.String(20), nullable=False),
        sa.Column('content', sa.Text, nullable=False),
        sa.Column('message_type', sa.String(50), server_default='sms'),
        sa.Column('intent', sa.String(50), nullable=True),
        sa.Column('sentiment', sa.String(50), nullable=True),
        sa.Column('metadata', postgresql.JSONB, server_default='{}'),
        sa.Column('provider', sa.String(50), nullable=True),
        sa.Column('provider_message_sid', sa.String(255), nullable=True),
        sa.Column('delivery_status', sa.String(50), nullable=True),
        sa.Column('delivery_error_code', sa.String(50), nullable=True),
        sa.Column('delivery_error_message', sa.Text, nullable=True),
        sa.Column('delivered_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('idx_messages_conversation', 'messages', ['conversation_id', 'created_at'])
    op.create_index('idx_messages_tenant_sender', 'messages', ['tenant_id', 'sender'])
    op.create_index('ix_messages_provider_message_sid', 'messages', ['provider_message_sid'])

    # Create appointments table
    op.create_table(
        'appointments',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('tenant_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('tenants.id'), nullable=False),
        sa.Column('lead_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('leads.id'), nullable=False),
        sa.Column('agent_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('agents.id'), nullable=False),
        sa.Column('conversation_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('conversations.id'), nullable=True),
        sa.Column('start_time', sa.DateTime(timezone=True), nullable=False),
        sa.Column('end_time', sa.DateTime(timezone=True), nullable=False),
        sa.Column('status', sa.String(50), server_default='confirmed'),
        sa.Column('disposition', sa.String(50), nullable=True),
        sa.Column('notes', sa.Text, nullable=True),
        sa.Column('call_duration_seconds', sa.Integer, nullable=True),
        sa.Column('reminder_24h_sent', sa.Boolean, server_default='false'),
        sa.Column('reminder_1h_sent', sa.Boolean, server_default='false'),
        sa.Column('reminder_15m_sent', sa.Boolean, server_default='false'),
        sa.Column('cancelled_reason', sa.String(255), nullable=True),
        sa.Column('rescheduled_from', postgresql.UUID(as_uuid=True), sa.ForeignKey('appointments.id'), nullable=True),
        sa.Column('booking_source', sa.String(50), server_default='ai'),
        sa.Column('ai_confidence', sa.Float, nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now()),
    )
    op.create_index('idx_appointments_agent_time', 'appointments', ['agent_id', 'start_time'])
    op.create_index('idx_appointments_tenant_status', 'appointments', ['tenant_id', 'status'])
    op.create_index('idx_appointments_lead', 'appointments', ['tenant_id', 'lead_id'])
    op.execute("""
        ALTER TABLE appointments
        ADD CONSTRAINT exclude_overlapping_appointments
        EXCLUDE USING gist (
            agent_id WITH =,
            tstzrange(start_time, end_time) WITH &&
        ) WHERE (status IN ('pending', 'confirmed'))
    """)

    # Create agent_availability table
    op.create_table(
        'agent_availability',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('agent_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('agents.id'), nullable=False, index=True),
        sa.Column('tenant_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('tenants.id'), nullable=False, index=True),
        sa.Column('start_time', sa.DateTime(timezone=True), nullable=False),
        sa.Column('end_time', sa.DateTime(timezone=True), nullable=False),
        sa.Column('availability_status', sa.String(50), server_default='available'),
        sa.Column('recurrence_rule', sa.String(255), nullable=True),
        sa.Column('notes', sa.String(255), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now()),
    )

    # Create audit_logs table
    op.create_table(
        'audit_logs',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('tenant_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('tenants.id'), nullable=False),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('action', sa.String(100), nullable=False),
        sa.Column('resource_type', sa.String(100)),
        sa.Column('resource_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('details', postgresql.JSONB, server_default='{}'),
        sa.Column('ip_address', sa.String(45), nullable=True),
        sa.Column('user_agent', sa.Text, nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('idx_audit_logs_tenant_action', 'audit_logs', ['tenant_id', 'action'])
    op.create_index('idx_audit_logs_tenant_resource', 'audit_logs', ['tenant_id', 'resource_type'])
    op.create_index('idx_audit_logs_tenant_created', 'audit_logs', ['tenant_id', 'created_at'])
    op.create_index('idx_audit_logs_user', 'audit_logs', ['user_id'])

    # Create call recording and QA analysis tables
    op.create_table(
        'call_recordings',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('tenant_id', sa.String(), nullable=False),
        sa.Column('appointment_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('appointments.id'), nullable=True),
        sa.Column('lead_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('leads.id'), nullable=True),
        sa.Column('agent_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('agents.id'), nullable=True),
        sa.Column('twilio_call_sid', sa.String(), nullable=True),
        sa.Column('twilio_recording_sid', sa.String(), nullable=True),
        sa.Column('audio_url', sa.String(), nullable=True),
        sa.Column('duration_seconds', sa.Integer, server_default='0'),
        sa.Column('channels', sa.Integer, server_default='1'),
        sa.Column('status', sa.String(), server_default='pending'),
        sa.Column('recording_metadata', sa.JSON, server_default='{}'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now()),
    )
    op.create_index('ix_call_recordings_tenant_id', 'call_recordings', ['tenant_id'])
    op.create_index('ix_call_recordings_appointment_id', 'call_recordings', ['appointment_id'])
    op.create_index('ix_call_recordings_lead_id', 'call_recordings', ['lead_id'])
    op.create_index('ix_call_recordings_agent_id', 'call_recordings', ['agent_id'])
    op.create_index('ix_call_recordings_twilio_call_sid', 'call_recordings', ['twilio_call_sid'])

    op.create_table(
        'call_transcripts',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('tenant_id', sa.String(), nullable=False),
        sa.Column('recording_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('call_recordings.id'), nullable=False),
        sa.Column('appointment_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('appointments.id'), nullable=True),
        sa.Column('lead_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('leads.id'), nullable=True),
        sa.Column('agent_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('agents.id'), nullable=True),
        sa.Column('full_text', sa.Text, nullable=True),
        sa.Column('segments', sa.JSON, server_default='[]'),
        sa.Column('language', sa.String(), server_default='en'),
        sa.Column('transcription_service', sa.String(), server_default='whisper'),
        sa.Column('total_words', sa.Integer, server_default='0'),
        sa.Column('customer_words', sa.Integer, server_default='0'),
        sa.Column('agent_words', sa.Integer, server_default='0'),
        sa.Column('talk_ratio', sa.Float, server_default='0.5'),
        sa.Column('transcription_metadata', sa.JSON, server_default='{}'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('ix_call_transcripts_tenant_id', 'call_transcripts', ['tenant_id'])
    op.create_index('ix_call_transcripts_recording_id', 'call_transcripts', ['recording_id'])
    op.create_index('ix_call_transcripts_appointment_id', 'call_transcripts', ['appointment_id'])
    op.create_index('ix_call_transcripts_lead_id', 'call_transcripts', ['lead_id'])
    op.create_index('ix_call_transcripts_agent_id', 'call_transcripts', ['agent_id'])

    op.create_table(
        'call_analysis',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('tenant_id', sa.String(), nullable=False),
        sa.Column('transcript_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('call_transcripts.id'), nullable=False),
        sa.Column('recording_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('call_recordings.id'), nullable=True),
        sa.Column('appointment_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('appointments.id'), nullable=True),
        sa.Column('lead_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('leads.id'), nullable=True),
        sa.Column('agent_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('agents.id'), nullable=True),
        sa.Column('objections_detected', sa.JSON, server_default='[]'),
        sa.Column('objection_count', sa.Integer, server_default='0'),
        sa.Column('objections_handled', sa.Integer, server_default='0'),
        sa.Column('overall_sentiment', sa.String(), server_default='neutral'),
        sa.Column('sentiment_score', sa.Float, server_default='0.5'),
        sa.Column('sentiment_timeline', sa.JSON, server_default='[]'),
        sa.Column('engagement_score', sa.Float, server_default='0.5'),
        sa.Column('interruption_count', sa.Integer, server_default='0'),
        sa.Column('silence_periods', sa.Integer, server_default='0'),
        sa.Column('questions_asked', sa.Integer, server_default='0'),
        sa.Column('compliance_violations', sa.JSON, server_default='[]'),
        sa.Column('compliance_score', sa.Float, server_default='1.0'),
        sa.Column('key_points', sa.JSON, server_default='[]'),
        sa.Column('next_steps', sa.JSON, server_default='[]'),
        sa.Column('probability_to_close', sa.Float, server_default='0.5'),
        sa.Column('analysis_metadata', sa.JSON, server_default='{}'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('ix_call_analysis_tenant_id', 'call_analysis', ['tenant_id'])
    op.create_index('ix_call_analysis_transcript_id', 'call_analysis', ['transcript_id'])
    op.create_index('ix_call_analysis_appointment_id', 'call_analysis', ['appointment_id'])
    op.create_index('ix_call_analysis_lead_id', 'call_analysis', ['lead_id'])
    op.create_index('ix_call_analysis_agent_id', 'call_analysis', ['agent_id'])


def downgrade() -> None:
    op.drop_table('call_analysis')
    op.drop_table('call_transcripts')
    op.drop_table('call_recordings')
    op.drop_table('audit_logs')
    op.drop_table('agent_availability')
    op.drop_table('appointments')
    op.drop_table('messages')
    op.drop_table('conversations')
    op.drop_table('leads')
    op.drop_table('campaigns')
    op.drop_table('agents')
    op.drop_table('users')
    op.drop_table('tenants')
