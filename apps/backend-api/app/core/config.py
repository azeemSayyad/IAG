from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=True,
        extra="ignore",
    )

    # App
    APP_ENV: str = "development"
    NODE_ENV: str = ""
    PORT: int = 0
    APP_URL: str = ""
    APP_PORT: int = 8000
    FRONTEND_URL: str = "http://localhost:3000"
    ALLOWED_ORIGINS: str = ""
    AUTH_ENABLED: bool = True
    MY_NUMBER_CHIP_ENABLED: bool = False
    CHAT_TANK_ENABLED: bool = False
    INBOUND_THREAD_ROUTING_ENABLED: bool = True
    SEED_AGENT_LIMIT: int = 0

    # Database
    POSTGRES_URL: str = ""
    DATABASE_URL: str = "postgresql://postgres:postgres@localhost:5432/launchpad"

    # Redis
    REDIS_HOST: str = ""
    REDIS_PASSWORD: str = ""
    REDIS_PORT: int = 6379
    REDIS_URL: str = "redis://localhost:6379/0"

    # JWT
    JWT_SECRET: str = "change-me-in-production"
    JWT_EXPIRES_IN: str = ""
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # Legacy provider metadata
    TWILIO_ACCOUNT_SID: str = ""
    TWILIO_AUTH_TOKEN: str = ""
    TWILIO_PHONE_NUMBER: str = ""
    TWILIO_MESSAGING_SERVICE_SID: str = ""
    TWILIO_DEFAULT_AGENT_USER_ID: str = ""
    TWILIO_SYSTEM_USER_ID: str = ""
    PUBLIC_API_URL: str = ""

    # SignWell e-signature (onboarding agreement + W-9 embedded signing)
    SIGNWELL_API_KEY: str = ""
    SIGNWELL_BASE_URL: str = "https://www.signwell.com/api/v1"
    SIGNWELL_AGREEMENT_TEMPLATE_ID: str = "09277cb9-dc78-4087-89a2-a6ecba0bef77"  # pre-signed by agency
    SIGNWELL_W9_TEMPLATE_ID: str = "70643684-1019-4ae6-b13d-c3d6dad90b88"
    SIGNWELL_PLACEHOLDER_NAME: str = "Agent"
    # Optional: API application id that whitelists embedded-signing domains.
    SIGNWELL_API_APP_ID: str = ""
    # Non-billable test documents while developing (free plan = 25 real docs/mo).
    SIGNWELL_TEST_MODE: bool = False
    # Path to the agency's authorized-signature PNG (transparent bg) used to
    # counter-stamp the completed agreement. Relative paths resolve from this file.
    SIGNWELL_AGENCY_SIGNATURE_PATH: str = "app/onboarding/assets/agency_signature.png"

    # Engage Clouds  (provider key "sinch" — the original, working lead-SMS pipeline)
    ENGAGECLOUD_API_KEY: str = ""
    ENGAGECLOUD_API_SECRET: str = ""
    ENGAGECLOUD_AGENCY_ID: str = ""
    ENGAGE_CLOUD_WEBHOOK_SECRET: str = ""
    ENGAGECLOUD_FROM_NUMBERS: str = ""
    ENGAGECLOUD_USE_NEW_AUTH: bool = False
    ENGAGECLOUD_API_BASE_URL: str = "https://eu.app.api.sinch.com/v1"
    ENGAGECLOUD_SMS_SOURCE: str = "messagemedia"

    # ENGAGE2 — a SECOND, fully independent lead-SMS provider ("Engage Cloud" chip),
    # mirroring the Sinch pipeline on its OWN account: different api key/secret, base
    # URL, webhook secret and DID numbers. All blank by default => the provider is
    # DORMANT and Sinch is completely unaffected. Fill these (Render env) to enable it.
    ENGAGE2_API_KEY: str = ""
    ENGAGE2_API_SECRET: str = ""
    ENGAGE2_AGENCY_ID: str = ""
    ENGAGE2_WEBHOOK_SECRET: str = ""
    ENGAGE2_FROM_NUMBERS: str = ""
    ENGAGE2_USE_NEW_AUTH: bool = False
    ENGAGE2_API_BASE_URL: str = "https://eu.app.api.sinch.com/v1"
    ENGAGE2_SMS_SOURCE: str = "messagemedia"

    # SMS Queue feature: when False, agent/manager "Send" records the message
    # locally without hitting Sinch (safe for dev). Flip to True in prod to send
    # real texts via communication_provider. Also gates the auto-sync ingest task.
    SMS_LIVE_SEND_ENABLED: bool = False
    SMS_QUEUE_AUTOSYNC_ENABLED: bool = True

    # Applicant Inbox (admin↔hiree SMS): a DEDICATED Sinch sub-account number pool
    # (ACAHelplineChannel_PLM_0002 / +1 772 315 0752) reserved ONLY for texting
    # hirees — it is filtered OUT of the lead sender pool so a lead blast can never
    # use it, and inbound replies to it route to the applicant inbox. Comma-separated;
    # the first/rotated number is the sender. APPLICANT_SMS_FROM_NUMBER is the legacy
    # single value (kept for display / fallback).
    APPLICANT_SMS_FROM_NUMBERS: str = "17723150752"
    APPLICANT_SMS_FROM_NUMBER: str = "17723150752"
    # Live-send admin→hiree texts when the provider is configured (else record
    # locally, as in dev). Independent of the LEAD send path + its first-template
    # lockdown — this is a separate recruiting channel.
    APPLICANT_SMS_LIVE_SEND_ENABLED: bool = True
    # --- Hiree Engage Cloud account — its OWN, independent credentials so admin↔hiree
    # SMS runs separately from leads and never crosses the lead channel. Nothing falls
    # back to the lead ENGAGECLOUD_* — these are DIFFERENT vars with DIFFERENT values.
    # Auth: either HTTP basic auth (api key + secret) OR a ready-made Authorization
    # header (APPLICANT_ENGAGECLOUD_AUTH_HEADER) — if the header is set it takes
    # precedence. Leave blank in dev to record locally; set in prod. The webhook secret
    # validates the dedicated inbound POST /webhooks/applicant-engage.
    APPLICANT_ENGAGECLOUD_API_BASE_URL: str = "https://eu.app.api.sinch.com/v1"  # base_uri
    APPLICANT_ENGAGECLOUD_API_KEY: str = ""           # api_key
    APPLICANT_ENGAGECLOUD_API_SECRET: str = ""        # api_secret
    APPLICANT_ENGAGECLOUD_AUTH_HEADER: str = ""       # authorization_header (raw, e.g. "Basic abc…")
    APPLICANT_ENGAGE_CLOUD_WEBHOOK_SECRET: str = ""   # validates POST /webhooks/applicant-engage

    # Ollama
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "mistral"

    # Sinch Voice & Video (WebRTC browser calling) — all env-driven, no hardcoding.
    # Values are supplied via .env / platform config; the system degrades
    # gracefully (calling disabled, clear "not configured" responses) when blank.
    SINCH_APP_KEY: str = ""
    SINCH_APP_SECRET: str = ""
    SINCH_PROJECT_ID: str = ""
    SINCH_REGION: str = "us"            # us | eu
    SINCH_VOICE_CALLBACK_SECRET: str = ""   # shared secret to validate Sinch voice webhooks
    SINCH_VOICE_API_BASE_URL: str = "https://calling.api.sinch.com"
    # Recording disclosure played to the lead before connecting (compliance).
    CALL_RECORDING_DISCLOSURE: str = "This call may be recorded for quality and training purposes."
    CALL_RECORDING_ENABLED: bool = True
    # Public base URL Sinch should call back to (voice webhooks / SVAML).
    VOICE_CALLBACK_BASE_URL: str = ""   # e.g. https://getquotedtoday.com

    # AWS / storage  (call recordings are downloaded from Sinch and stored here
    # permanently — Sinch is NOT the permanent store).
    AWS_REGION: str = ""
    AWS_ACCESS_KEY_ID: str = ""
    AWS_SECRET_ACCESS_KEY: str = ""
    S3_BUCKET_NAME: str = ""
    S3_BUCKET: str = ""
    AWS_S3_BUCKET: str = ""             # alias accepted from .env
    AWS_S3_REGION: str = ""             # alias accepted from .env
    S3_RECORDINGS_PREFIX: str = "call-recordings"

    # Stripe
    STRIPE_SECRET_KEY: str = ""
    STRIPE_WEBHOOK_SECRET: str = ""

    # Email
    MAIL_USER: str = ""
    MAIL_PASS: str = ""
    EMAIL_IMAP_PASSWORD: str = ""
    EMAIL_SYSTEM_USER_ID: str = ""

    # Sender pool (300+ numbers) — per-number daily cap for 10DLC exhaustion guard
    SENDER_DAILY_CAP: int = 2000

    # Outbound rate limits (raised for production volume; all env-tunable).
    # Sized for the 300-number pool (300 x 2000/day = 600k/day ~ 25k/hr peak)
    # with headroom. per_lead keeps an anti-spam guard.
    RATE_LIMIT_PER_LEAD_PER_DAY: int = 5
    RATE_LIMIT_PER_LEAD_INTERVAL_SECONDS: int = 60
    RATE_LIMIT_PER_TENANT_PER_HOUR: int = 50000
    RATE_LIMIT_GLOBAL_PER_HOUR: int = 200000

    # Google Calendar (optional)
    GOOGLE_CALENDAR_CREDENTIALS: str = ""

    # Scheduling (agent timezone = source of truth; lead timezone = display only)
    AGENT_TZ: str = "America/New_York"
    AGENT_START_HOUR: int = 8           # 8:00 AM ET
    AGENT_END_HOUR: int = 21            # 9:00 PM ET (last slot start 20:45)
    LUNCH_START_HOUR: int = 14          # 2:00 PM ET
    LUNCH_END_HOUR: int = 15            # 3:00 PM ET
    SLOT_MINUTES: int = 15
    SCHEDULING_SKIP_WEEKENDS: bool = True
    SCHEDULING_SKIP_HOLIDAYS: bool = True   # skip US federal holidays (computed per year)
    SCHEDULING_AUTO_ROLLOVER: bool = True

    # State-license-aware booking — DISABLED by default so the live pipeline is
    # unchanged until every served state has an ACTIVE-licensed agent (backfill
    # first). Flip STATE_LICENSE_BOOKING_ENFORCED=true (env) to enforce: the
    # booking slots offered to a lead (and the agent the appointment lands with)
    # are drawn ONLY from agents licensed for the lead's CSV state. Leads in a
    # state with no licensed agent are held (no slots) and flagged for an admin.
    # Leads with NO state fall back to today's behavior (any active agent).
    STATE_LICENSE_BOOKING_ENFORCED: bool = False


    # ---- Appointment Capacity Engine (same-day lead pacing) ----
    # DISABLED by default: with SAME_DAY_PACING_ENABLED=false the CSV import keeps
    # blasting outreach exactly as today. When true, imported leads are HELD and a
    # capacity-aware controller releases only enough top leads per state to fill
    # that day's appointment slots. See docs/APPOINTMENT_CAPACITY_ENGINE*.md.
    SAME_DAY_PACING_ENABLED: bool = False
    # When true the controller only LOGS what it would release (no real enqueue),
    # so the math can be validated with zero behavior change. Set false to send
    # for real once validated.
    PACING_DRY_RUN: bool = False
    PACING_CYCLE_MINUTES: int = 15          # controller top-up interval
    # Lead-local hour after which the engine stops releasing NEW first-touch
    # outreach. 21 = 9 PM, the TCPA legal cut-off, so outreach runs through the
    # afternoon/evening (not stopped at 4 PM); replies still book same-day until
    # the agent day ends.
    OUTREACH_CUTOFF_HOUR: int = 21
    PACING_WAVE_BUFFER: float = 0.10        # per-wave safety overshoot (fraction)
    PACING_SHOW_FLOOR: float = 0.5          # lower bound on show-rate (caps over-booking)
    TARGET_UTILIZATION: float = 1.0         # fill fraction that counts as "full"
    FUTURE_DAY_FALLBACK_ENABLED: bool = True
    PACING_DEFAULT_REPLY_RATE: float = 0.20
    PACING_DEFAULT_BOOK_RATE: float = 0.50
    PACING_DEFAULT_SHOW_RATE: float = 0.80
    PACING_FUNNEL_WINDOW_DAYS: int = 21     # rolling window for measured funnel rates

    # --- Capacity-sized pacing (P1-P3): size the drip to live free-agent demand ---
    # OFF by default, so the existing drip behaviour is unchanged until explicitly
    # turned on. When true, drip_cycle releases at most (free licensed agents x
    # CAPACITY_BUFFER) leads per tick and sends NOTHING into a state with no active
    # licensed agent. "Free" = an SMS-queue agent in AVAILABLE; ON_CALL (accepted a
    # lead -> busy, no telephony) does not count. The first-template lockdown is
    # unaffected — capacity only changes how many / which leads are released.
    CAPACITY_PACING_ENABLED: bool = False
    CAPACITY_BUFFER: float = 1.5            # fresh leads per free agent (per-tick ceiling)
    CAPACITY_STATES: str = "NC,SC,FL,TX,GA" # states the engine paces / gates on

    # --- Multi-carrier DID failover (CA-CF): numbers grouped by carrier, with
    # automatic overflow to the next carrier when one hits its safe limit. Empty ->
    # a single 'sinch' carrier from ENGAGECLOUD_FROM_NUMBERS (today's behaviour).
    # JSON: [{"name","priority","role":"primary"|"reserve","daily_cap","mps","numbers":[...]}]
    CARRIER_POOLS_JSON: str = ""
    # Use reserve/safety carriers once primary fleet usage crosses this fraction.
    CARRIER_RESERVE_HIGH_WATER: float = 0.8
    # Per-carrier circuit breaker (CD): trip a whole carrier (skip ALL its numbers,
    # overflow to others) when its failure rate over the window exceeds this, until
    # it recovers. Needs a minimum sample so a cold carrier isn't tripped on noise.
    CARRIER_BREAKER_FAIL_RATE: float = 0.5
    CARRIER_BREAKER_MIN_SAMPLE: int = 20

    # --- Lead fatigue (P4): per-phone frequency cap + cooldown across campaigns,
    # so a person isn't over-texted on re-runs (top cause of spam complaints ->
    # cold-number death). OFF by default; enforced at the first-template send gate
    # alongside the existing per-lead rate-limit. Redis-based (no migration).
    FATIGUE_ENABLED: bool = False
    FATIGUE_FREQ_CAP: int = 4               # max first-template sends per phone, ever
    FATIGUE_COOLDOWN_HOURS: int = 72        # min gap before re-texting the same phone

    # --- DID-fleet dashboard (provisioning forecast) ---
    # "Need new DID in X days": project when daily demand will cross this fraction of
    # a carrier's capacity, trended over the rolling history window.
    DID_PROVISION_THRESHOLD: float = 0.8
    DID_FORECAST_WINDOW_DAYS: int = 14

    # --- Recipient-carrier caps + working hours (DID Fleet enforcement) ---
    # Daily cap counters bucket on PACIFIC local time and reset at midnight
    # America/Los_Angeles — keyed on the local Pacific date, so the boundary
    # auto-tracks PST<->PDT with no one-hour drift. T-Mobile is the only recipient
    # carrier with its OWN per-provider cap; every send also counts toward the
    # provider total. Here "provider" = a send-side sender pool (a carrier_registry
    # pool, e.g. sinch), NOT the recipient's carrier.
    CAP_RESET_TZ: str = "America/Los_Angeles"     # daily cap reset boundary (Pacific, DST-safe)
    PROVIDER_DAILY_CAP: int = 4000                # per-provider total sends/day (0 = unlimited)
    TMOBILE_PER_PROVIDER_CAP: int = 2000          # T-Mobile sends per provider per day
    # Recipient-carrier names (aliases) that count as T-Mobile.
    TMOBILE_CARRIER_NAMES: str = "tmobile,t-mobile,t-mobile us,metropcs,metro by t-mobile"
    # Providers (sender pools) that carry T-Mobile traffic — for a rare carrier-SPECIFIC
    # route. Comma-separated pool names. Normally unused: channels are MIXED, so the
    # recipient carrier is resolved per-number (carrier_lookup), not per provider.
    TMOBILE_PROVIDERS: str = ""
    # How long a looked-up number->carrier mapping is cached (carrier rarely changes),
    # so a blast resolves each number at most once instead of N live lookups.
    CARRIER_LOOKUP_CACHE_DAYS: int = 30
    # Generic number-lookup connector — plug in ANY carrier-lookup service with env
    # vars only, no code. Off until CARRIER_LOOKUP_URL is set. Used ONLY by background
    # enrichment (never the hot send path). Put real keys in the environment, not code.
    CARRIER_LOOKUP_URL: str = ""            # endpoint; "{number}" is replaced with the digits
    CARRIER_LOOKUP_METHOD: str = "GET"      # "GET" or "POST"
    CARRIER_LOOKUP_AUTH: str = ""           # convenience: value for the Authorization header
    CARRIER_LOOKUP_HEADERS: str = ""        # JSON object of extra headers, e.g. {"X-API-Key":"abc"}
    CARRIER_LOOKUP_BODY: str = ""           # POST only: JSON body template ("{number}" allowed)
    CARRIER_LOOKUP_FIELD: str = "carrier"   # dotted path to the carrier in the JSON response
                                            #   e.g. "carrier" or "data.carrier.name"
    # Working hours: first-touch outreach only runs inside this window, in Eastern
    # local time (DST-safe — tracks EST/EDT). 10 = 10 AM, 19 = 7 PM; Mon-Fri only.
    WORKING_HOURS_TZ: str = "America/New_York"
    WORKING_HOURS_START: int = 10                 # 10 AM ET (inclusive)
    WORKING_HOURS_END: int = 19                   # 7 PM ET (exclusive)
    WORKING_DAYS: str = "0,1,2,3,4"               # Python weekday(): Mon=0 .. Fri=4
    # Enforcement switches (UI-toggleable via engine_flags). ALL OFF = observe-only:
    # caps / dedup / working-hours are computed + recorded for the dashboard but never
    # block a send until the matching switch is on. The first-template lockdown is
    # NEVER affected by any of these.
    CARRIER_CAPS_ENFORCE: bool = False            # enforce provider total + T-Mobile per-provider caps
    TMOBILE_DEDUP_ENFORCE: bool = False           # enforce T-Mobile cross-provider no-double-send
    WORKING_HOURS_ENFORCE: bool = False           # enforce the 10-7 ET Mon-Fri window

    def model_post_init(self, __context) -> None:
        if self.NODE_ENV and self.APP_ENV == "development":
            self.APP_ENV = self.NODE_ENV
        if self.PORT:
            self.APP_PORT = self.PORT
        if self.APP_URL:
            self.FRONTEND_URL = self.APP_URL
        if self.JWT_EXPIRES_IN:
            try:
                self.JWT_ACCESS_TOKEN_EXPIRE_MINUTES = int(self.JWT_EXPIRES_IN)
            except ValueError:
                if self.JWT_EXPIRES_IN.endswith("m"):
                    self.JWT_ACCESS_TOKEN_EXPIRE_MINUTES = int(self.JWT_EXPIRES_IN[:-1])
                elif self.JWT_EXPIRES_IN.endswith("h"):
                    self.JWT_ACCESS_TOKEN_EXPIRE_MINUTES = int(self.JWT_EXPIRES_IN[:-1]) * 60
        if self.POSTGRES_URL:
            self.DATABASE_URL = self.POSTGRES_URL
        # Railway/Heroku-style providers hand out "postgres://..." URLs, but
        # SQLAlchemy 2.x + psycopg2 require the "postgresql://" scheme. Normalize
        # so the same env var works on Railway without manual editing.
        if self.DATABASE_URL and self.DATABASE_URL.startswith("postgres://"):
            self.DATABASE_URL = "postgresql://" + self.DATABASE_URL[len("postgres://"):]
        if self.S3_BUCKET_NAME and not self.S3_BUCKET:
            self.S3_BUCKET = self.S3_BUCKET_NAME
        # Accept the AWS_S3_BUCKET / AWS_S3_REGION env names too (caller's spec),
        # normalizing onto the canonical S3_BUCKET / AWS_REGION used internally.
        if self.AWS_S3_BUCKET and not self.S3_BUCKET:
            self.S3_BUCKET = self.AWS_S3_BUCKET
        if self.AWS_S3_REGION and not self.AWS_REGION:
            self.AWS_REGION = self.AWS_S3_REGION
        if self.REDIS_HOST:
            auth = f":{self.REDIS_PASSWORD}@" if self.REDIS_PASSWORD else ""
            self.REDIS_URL = f"redis://{auth}{self.REDIS_HOST}:{self.REDIS_PORT}/0"


settings = Settings()
