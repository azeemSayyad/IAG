-- ClickHouse Analytics Schema
-- Optimized for fast aggregations and time-series analysis

-- Event tracking table
CREATE TABLE IF NOT EXISTS analytics.events (
    event_id UUID DEFAULT generateUUIDv4(),
    tenant_id UUID,
    event_type String,
    event_category String,
    user_id UUID,
    lead_id UUID,
    appointment_id UUID,
    campaign_id UUID,
    agent_id UUID,
    properties Map(String, String),
    created_at DateTime DEFAULT now(),
    date Date DEFAULT toDate(created_at)
) ENGINE = MergeTree()
PARTITION BY toYYYYMM(date)
ORDER BY (tenant_id, event_type, date, created_at)
TTL date + INTERVAL 1 YEAR;

-- Lead metrics table
CREATE TABLE IF NOT EXISTS analytics.lead_metrics (
    tenant_id UUID,
    lead_id UUID,
    source String,
    state String,
    lead_score UInt32,
    status String,
    contacted_at DateTime,
    replied_at Nullable(DateTime),
    booked_at Nullable(DateTime),
    completed_at Nullable(DateTime),
    won_at Nullable(DateTime),
    reply_time_seconds Nullable(UInt32),
    booking_time_seconds Nullable(UInt32),
    date Date DEFAULT toDate(contacted_at)
) ENGINE = MergeTree()
PARTITION BY toYYYYMM(date)
ORDER BY (tenant_id, source, date);

-- Appointment metrics table
CREATE TABLE IF NOT EXISTS analytics.appointment_metrics (
    tenant_id UUID,
    appointment_id UUID,
    lead_id UUID,
    agent_id UUID,
    campaign_id UUID,
    start_time DateTime,
    end_time DateTime,
    status String,
    disposition Nullable(String),
    call_duration_seconds Nullable(UInt32),
    no_show Bool DEFAULT false,
    date Date DEFAULT toDate(start_time)
) ENGINE = MergeTree()
PARTITION BY toYYYYMM(date)
ORDER BY (tenant_id, agent_id, date);

-- Message metrics table
CREATE TABLE IF NOT EXISTS analytics.message_metrics (
    tenant_id UUID,
    message_id UUID,
    conversation_id UUID,
    lead_id UUID,
    sender String,
    intent Nullable(String),
    sentiment Nullable(String),
    message_length UInt32,
    has_reply Bool DEFAULT false,
    reply_time_seconds Nullable(UInt32),
    created_at DateTime,
    date Date DEFAULT toDate(created_at)
) ENGINE = MergeTree()
PARTITION BY toYYYYMM(date)
ORDER BY (tenant_id, sender, date);

-- Agent performance table
CREATE TABLE IF NOT EXISTS analytics.agent_performance (
    tenant_id UUID,
    agent_id UUID,
    date Date,
    total_appointments UInt32 DEFAULT 0,
    completed_appointments UInt32 DEFAULT 0,
    won_appointments UInt32 DEFAULT 0,
    no_show_appointments UInt32 DEFAULT 0,
    total_call_minutes UInt32 DEFAULT 0,
    utilization_percent Float32 DEFAULT 0
) ENGINE = SummingMergeTree()
PARTITION BY toYYYYMM(date)
ORDER BY (tenant_id, agent_id, date);

-- Campaign performance table
CREATE TABLE IF NOT EXISTS analytics.campaign_performance (
    tenant_id UUID,
    campaign_id UUID,
    date Date,
    leads_created UInt32 DEFAULT 0,
    leads_contacted UInt32 DEFAULT 0,
    leads_replied UInt32 DEFAULT 0,
    leads_booked UInt32 DEFAULT 0,
    leads_completed UInt32 DEFAULT 0,
    leads_won UInt32 DEFAULT 0,
    total_sms_sent UInt32 DEFAULT 0
) ENGINE = SummingMergeTree()
PARTITION BY toYYYYMM(date)
ORDER BY (tenant_id, campaign_id, date);

-- Materialized views for fast queries

-- Hourly aggregation view
CREATE MATERIALIZED VIEW IF NOT EXISTS analytics.hourly_metrics
ENGINE = SummingMergeTree()
PARTITION BY toYYYYMM(hour)
ORDER BY (tenant_id, event_type, hour)
AS SELECT
    tenant_id,
    event_type,
    toStartOfHour(created_at) as hour,
    count() as event_count
FROM analytics.events
GROUP BY tenant_id, event_type, hour;

-- Daily lead summary view
CREATE MATERIALIZED VIEW IF NOT EXISTS analytics.daily_lead_summary
ENGINE = SummingMergeTree()
PARTITION BY toYYYYMM(date)
ORDER BY (tenant_id, source, date)
AS SELECT
    tenant_id,
    source,
    date,
    count() as total_leads,
    countIf(status = 'replied') as replied_leads,
    countIf(status = 'booked') as booked_leads,
    countIf(status = 'completed') as completed_leads
FROM analytics.lead_metrics
GROUP BY tenant_id, source, date;
