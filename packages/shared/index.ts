// Shared constants and utilities for Launchpad Call Center

// --- Lead Statuses ---
export const LEAD_STATUSES = [
  'new',
  'contacted',
  'replied',
  'qualified',
  'booked',
  'completed',
  'unqualified',
  'no_show',
  'nurture',
] as const;

export type LeadStatus = (typeof LEAD_STATUSES)[number];

// --- Appointment Statuses ---
export const APPOINTMENT_STATUSES = [
  'pending',
  'confirmed',
  'completed',
  'cancelled',
  'no_show',
  'rescheduled',
] as const;

export type AppointmentStatus = (typeof APPOINTMENT_STATUSES)[number];

// --- Dispositions ---
export const DISPOSITIONS = ['won', 'lost', 'follow_up', 'no_answer'] as const;
export type Disposition = (typeof DISPOSITIONS)[number];

export const DISPOSITION_CONFIG: Record<Disposition, { label: string; color: string; nextAction: string }> = {
  won: { label: 'Won', color: '#10b981', nextAction: 'onboarding' },
  lost: { label: 'Lost', color: '#ef4444', nextAction: 'nurture' },
  follow_up: { label: 'Follow Up', color: '#f59e0b', nextAction: 'schedule_followup' },
  no_answer: { label: 'No Answer', color: '#6b7280', nextAction: 'retry' },
};

// --- Intent Classes ---
export const INTENTS = [
  'POSITIVE',
  'INTERESTED',
  'SKEPTICAL',
  'NEGATIVE',
  'STOP',
  'BOOK_NOW',
  'QUESTION',
  'RESCHEDULE',
] as const;

export type Intent = (typeof INTENTS)[number];

export const INTENT_PRIORITY: Record<Intent, number> = {
  STOP: 0,
  BOOK_NOW: 0,
  POSITIVE: 1,
  INTERESTED: 2,
  RESCHEDULE: 2,
  SKEPTICAL: 3,
  QUESTION: 3,
  NEGATIVE: 5,
};

// --- User Roles ---
export const ROLES = ['super_admin', 'tenant_admin', 'manager', 'agent'] as const;
export type Role = (typeof ROLES)[number];

export const ROLE_HIERARCHY: Record<Role, number> = {
  super_admin: 4,
  tenant_admin: 3,
  manager: 2,
  agent: 1,
};

// --- Campaign Tones ---
export const CAMPAIGN_TONES = ['friendly', 'professional', 'casual', 'urgent'] as const;
export type CampaignTone = (typeof CAMPAIGN_TONES)[number];

// --- Business Hours ---
export const BUSINESS_START_HOUR = 10; // 10 AM
export const BUSINESS_END_HOUR = 21; // 9 PM
export const SLOT_DURATION_MINUTES = 15;
export const MAX_DAYS_AHEAD = 3;
export const SLOTS_PER_HOUR = 60 / SLOT_DURATION_MINUTES;

// --- Rate Limits ---
export const RATE_LIMITS = {
  PER_LEAD_PER_DAY: 3,
  PER_LEAD_INTERVAL_SECONDS: 60,
  PER_TENANT_PER_HOUR: 100,
  GLOBAL_PER_HOUR: 1000,
} as const;

// --- Score Tiers ---
export function getScoreTier(score: number): 'hot' | 'warm' | 'cool' | 'cold' {
  if (score >= 80) return 'hot';
  if (score >= 60) return 'warm';
  if (score >= 40) return 'cool';
  return 'cold';
}

export const SCORE_TIER_CONFIG = {
  hot: { label: 'Hot', color: '#ef4444', description: 'Immediate outreach, best agents' },
  warm: { label: 'Warm', color: '#f59e0b', description: 'Standard outreach' },
  cool: { label: 'Cool', color: '#3b82f6', description: 'Slower cadence' },
  cold: { label: 'Cold', color: '#6b7280', description: 'Nurture campaign' },
} as const;

// --- Time Formatting ---
export function formatTimeUntil(targetTime: string): string {
  const now = new Date();
  const target = new Date(targetTime);
  const diffMs = target.getTime() - now.getTime();
  const totalSeconds = Math.floor(diffMs / 1000);

  if (totalSeconds < 0) return 'passed';
  if (totalSeconds < 60) return `${totalSeconds}s`;
  if (totalSeconds < 3600) {
    const minutes = Math.floor(totalSeconds / 60);
    return `${minutes}m`;
  }
  if (totalSeconds < 86400) {
    const hours = Math.floor(totalSeconds / 3600);
    const minutes = Math.floor((totalSeconds % 3600) / 60);
    return `${hours}h ${minutes}m`;
  }
  const days = Math.floor(totalSeconds / 86400);
  const hours = Math.floor((totalSeconds % 86400) / 3600);
  return `${days}d ${hours}h`;
}

export function formatDisplayTime(isoString: string): string {
  return new Date(isoString).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

export function formatDisplayDate(isoString: string): string {
  return new Date(isoString).toLocaleDateString([], {
    weekday: 'long',
    year: 'numeric',
    month: 'long',
    day: 'numeric',
  });
}

// --- Phone Formatting ---
export function formatPhone(phone: string): string {
  const cleaned = phone.replace(/\D/g, '');
  if (cleaned.length === 11 && cleaned.startsWith('1')) {
    return `+1 (${cleaned.slice(1, 4)}) ${cleaned.slice(4, 7)}-${cleaned.slice(7)}`;
  }
  if (cleaned.length === 10) {
    return `(${cleaned.slice(0, 3)}) ${cleaned.slice(3, 6)}-${cleaned.slice(6)}`;
  }
  return phone;
}

export function maskPhone(phone: string): string {
  if (!phone || phone.length < 7) return phone;
  return phone.slice(0, 4) + '***' + phone.slice(-4);
}

// --- Validation ---
export function isValidEmail(email: string): boolean {
  return /^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$/.test(email);
}

export function isValidPhone(phone: string): boolean {
  const cleaned = phone.replace(/\D/g, '');
  return cleaned.length >= 10 && cleaned.length <= 11;
}
