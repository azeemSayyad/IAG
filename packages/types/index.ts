// Shared TypeScript interfaces for Launchpad Call Center
// Used by both frontend and backend-facing code

export interface Tenant {
  id: string;
  name: string;
  subscription_plan: 'starter' | 'growth' | 'enterprise';
  status: 'active' | 'suspended' | 'cancelled' | 'trial';
  max_agents: number;
  max_leads_per_month: number;
  settings: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  deleted_at: string | null;
}

export interface User {
  id: string;
  tenant_id: string;
  email: string;
  first_name: string;
  last_name: string;
  role: 'super_admin' | 'tenant_admin' | 'manager' | 'agent';
  status: 'active' | 'inactive' | 'locked' | 'invited';
  last_login_at: string | null;
  created_at: string;
  updated_at: string;
  deleted_at: string | null;
}

export interface Agent {
  id: string;
  tenant_id: string;
  user_id: string;
  timezone: string;
  daily_capacity: number;
  max_concurrent: number;
  skills: string[];
  weight: number;
  status: 'active' | 'inactive' | 'on_break' | 'offline';
  created_at: string;
  updated_at: string;
  user?: User;
}

export interface Lead {
  id: string;
  tenant_id: string;
  source: string;
  source_metadata: Record<string, unknown>;
  first_name: string;
  last_name: string;
  phone: string;
  phone_normalized: string | null;
  email: string | null;
  email_normalized: string | null;
  state: string | null;
  city: string | null;
  zip_code: string | null;
  timezone: string;
  lead_score: number;
  booking_probability: number;
  conversion_probability: number;
  lifecycle_stage: string;
  ai_status: string;
  status: 'new' | 'contacted' | 'replied' | 'qualified' | 'booked' | 'completed' | 'unqualified' | 'no_show' | 'nurture';
  campaign_id: string | null;
  tags: string[];
  custom_fields: Record<string, unknown>;
  last_contacted_at: string | null;
  consent_sms: boolean;
  consent_email: boolean;
  consent_call: boolean;
  consent_timestamp: string | null;
  created_at: string;
  updated_at: string;
  deleted_at: string | null;
}

export interface Conversation {
  id: string;
  tenant_id: string;
  lead_id: string;
  status: 'initiated' | 'active' | 'booking' | 'booked' | 'paused' | 'stopped' | 'closed';
  intent: string | null;
  sentiment: string | null;
  message_count: number;
  last_message_at: string | null;
  last_message_from: string | null;
  ai_context: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface Message {
  id: string;
  conversation_id: string;
  tenant_id: string;
  sender: 'customer' | 'ai';
  content: string;
  message_type: 'sms' | 'system' | 'notification';
  intent: string | null;
  sentiment: string | null;
  metadata: Record<string, unknown>;
  created_at: string;
}

export interface Appointment {
  id: string;
  tenant_id: string;
  lead_id: string;
  agent_id: string;
  conversation_id: string | null;
  start_time: string;
  end_time: string;
  status: 'pending' | 'confirmed' | 'completed' | 'cancelled' | 'no_show' | 'rescheduled';
  disposition: 'won' | 'lost' | 'follow_up' | 'no_answer' | null;
  notes: string | null;
  call_duration_seconds: number | null;
  reminder_24h_sent: boolean;
  reminder_1h_sent: boolean;
  reminder_15m_sent: boolean;
  cancelled_reason: string | null;
  rescheduled_from: string | null;
  created_at: string;
  updated_at: string;
  lead?: Lead;
  agent?: Agent;
}

export interface AgentAvailability {
  id: string;
  agent_id: string;
  tenant_id: string;
  start_time: string;
  end_time: string;
  availability_status: 'available' | 'booked' | 'break' | 'offline' | 'holiday';
  recurrence_rule: string | null;
  notes: string | null;
  created_at: string;
  updated_at: string;
}

export interface Campaign {
  id: string;
  tenant_id: string;
  name: string;
  description: string | null;
  status: 'active' | 'paused' | 'completed' | 'draft';
  tone: 'friendly' | 'professional' | 'casual' | 'urgent';
  prompt_template: string | null;
  objection_prompts: Record<string, string>;
  max_retries: number;
  retry_delay_hours: number;
  retry_tones: string[];
  booking_enabled: boolean;
  slot_duration_minutes: number;
  max_days_ahead: number;
  business_hours_start: number;
  business_hours_end: number;
  target_sources: string[];
  target_states: string[];
  min_lead_score: number;
  max_lead_score: number;
  total_leads: number;
  total_contacted: number;
  total_replied: number;
  total_booked: number;
  total_completed: number;
  total_won: number;
  created_at: string;
  updated_at: string;
  deleted_at: string | null;
}

export interface AuditLog {
  id: string;
  tenant_id: string;
  user_id: string | null;
  action: string;
  resource_type: string;
  resource_id: string | null;
  details: Record<string, unknown>;
  ip_address: string | null;
  user_agent: string | null;
  created_at: string;
}

export interface KPIData {
  agent_utilization: number;
  reply_rate: number;
  booking_rate: number;
  conversion_rate: number;
  no_show_rate: number;
  revenue_per_agent: number;
  cost_per_appointment: number;
  lead_to_booking_time: number;
}

export interface TimeSlot {
  start_time: string;
  end_time: string;
  start_display: string;
  end_display: string;
  is_available: boolean;
  agent_id: string | null;
}

export interface BookingResult {
  success: boolean;
  message: string;
  appointment_id?: string;
  agent_id?: string;
  options?: TimeSlot[];
}

export interface IntentResult {
  intent: string;
  confidence: number;
  method: 'fast' | 'llm';
  details: Record<string, unknown>;
}

export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  page: number;
  size: number;
}
