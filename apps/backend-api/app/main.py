from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.tenant import TenantIsolationMiddleware
from app.core.security_middleware import SecurityMiddleware
from app.core.metrics import MetricsMiddleware
from app.auth.routers.auth import router as auth_router
from app.leads.routers.leads import router as leads_router
from app.appointments.routers.appointments import router as appointments_router
from app.reports.routers.reports import router as reports_router
from app.audit.routers.audit import router as audit_router
from app.notifications.router import router as notifications_router
from app.ingestion.routers.ingestion import router as ingestion_router
from app.ai.routers.webhooks import router as webhook_router
from app.intent.routers.intent import router as intent_router
from app.booking.routers.booking import router as booking_router
from app.agent_os.routers.agent import router as agent_router
from app.followup.routers.followup import router as followup_router
from app.admin.routers.admin import router as admin_router
from app.realtime.router import router as realtime_router
from app.ml.router import router as ml_router
from app.security.router import router as security_router
from app.conversations.routers.conversations import router as conversations_router
from app.ai.routers.internal import router as internal_ai_router
from app.ai.conversation_engine.router import router as conversation_engine_router
from app.ai.conversation_engine.search_router import router as search_router
from app.pacing.router import router as pacing_router
from app.calls.routers.calls import router as calls_router
from app.coaching.routers.coaching import router as coaching_router
from app.workflows.routers.workflows import router as workflows_router
from app.compliance.router import router as compliance_router
from app.expenses.router import router as expenses_router
from app.contacts.router import router as contacts_router
from app.announcements.router import router as announcements_router
from app.sms_queue.routers.monitoring import router as sms_monitoring_router
from app.sms_queue.routers.manager import router as sms_manager_router
from app.sms_queue.routers.queue import router as sms_queue_router
from app.sales_dashboard.router import router as sales_dashboard_router
from app.onboarding.router import router as onboarding_router
from app.applicant_inbox.router import router as applicant_inbox_router
from app.direct_messages.router import router as direct_messages_router


def _cors_origins() -> list[str]:
    configured = [
        origin.strip()
        for origin in (settings.ALLOWED_ORIGINS or "").split(",")
        if origin.strip()
    ]
    defaults = [
        settings.FRONTEND_URL,
        "http://localhost:3000",
        "http://localhost:8080",
        "http://localhost:3001",
        "http://localhost:5500",
        "http://127.0.0.1:8080",
        "http://127.0.0.1:5500",
        "http://127.0.0.1:3000",
    ]
    # Local dev only: allow the standalone onboarding mockup opened straight off
    # disk (file:// → Origin "null") and Live Server, so it can POST to the API
    # without a CORS wall. Never enabled in production.
    if (settings.APP_ENV or "").lower() != "production":
        defaults += ["null", "http://127.0.0.1:3001", "http://localhost:5501", "http://127.0.0.1:5501"]
    return list(dict.fromkeys(configured + defaults))


app = FastAPI(
    title="Launchpad Call Center API",
    description="AI-Powered Insurance Call Center SaaS Platform",
    version="1.0.0",
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Tenant Isolation
app.add_middleware(TenantIsolationMiddleware)

# Security Middleware (SQL injection, XSS, path traversal protection)
app.add_middleware(SecurityMiddleware)

# Prometheus Metrics Middleware
app.add_middleware(MetricsMiddleware)

# Routers
app.include_router(auth_router, prefix="/api/v1")
app.include_router(leads_router, prefix="/api/v1")
app.include_router(appointments_router, prefix="/api/v1")
app.include_router(audit_router, prefix="/api/v1")
app.include_router(notifications_router, prefix="/api/v1")
app.include_router(ingestion_router, prefix="/api/v1")
app.include_router(webhook_router, prefix="/api/v1")
app.include_router(intent_router, prefix="/api/v1")
app.include_router(booking_router, prefix="/api/v1")
app.include_router(agent_router, prefix="/api/v1")
app.include_router(followup_router, prefix="/api/v1")
app.include_router(admin_router, prefix="/api/v1")
app.include_router(realtime_router, prefix="/api/v1")
app.include_router(ml_router, prefix="/api/v1")
app.include_router(security_router, prefix="/api/v1")
app.include_router(conversations_router, prefix="/api/v1")
app.include_router(internal_ai_router, prefix="/api/v1")
app.include_router(conversation_engine_router, prefix="/api/v1")
app.include_router(pacing_router, prefix="/api/v1")
app.include_router(search_router, prefix="/api/v1")
app.include_router(calls_router, prefix="/api/v1")
app.include_router(coaching_router, prefix="/api/v1")
app.include_router(workflows_router, prefix="/api/v1")
app.include_router(compliance_router, prefix="/api/v1")
app.include_router(expenses_router, prefix="/api/v1")
app.include_router(contacts_router, prefix="/api/v1")
app.include_router(announcements_router, prefix="/api/v1")
app.include_router(reports_router, prefix="/api/v1")
app.include_router(sms_monitoring_router, prefix="/api/v1")
app.include_router(sms_manager_router, prefix="/api/v1")
app.include_router(sms_queue_router, prefix="/api/v1")
app.include_router(sales_dashboard_router, prefix="/api/v1")
app.include_router(onboarding_router, prefix="/api/v1")
app.include_router(applicant_inbox_router, prefix="/api/v1")
app.include_router(direct_messages_router, prefix="/api/v1")


@app.get("/health")
def health_check():
    import os
    return {
        "status": "ok",
        "env": settings.APP_ENV,
        # Deployed git commit (Render injects RENDER_GIT_COMMIT) so a running
        # deploy's exact hash is verifiable over HTTP.
        "commit": (os.getenv("RENDER_GIT_COMMIT") or os.getenv("GIT_SHA")
                   or os.getenv("APP_VERSION") or "unknown"),
    }


@app.get("/metrics")
def metrics():
    from app.core.metrics import get_metrics
    from fastapi.responses import Response
    return Response(content=get_metrics(), media_type="text/plain")


# Serve the static frontend (single-service deploy): the UI and API share one
# origin, so api.js (which falls back to location.origin for its API base) works
# with no extra config. Mounted LAST, after all /api/v1, /health and /metrics
# routes, so those take precedence; everything else resolves to a static file.
from pathlib import Path as _Path
from fastapi.staticfiles import StaticFiles

_frontend_dir = _Path(__file__).resolve().parents[2] / "frontendall"
if _frontend_dir.is_dir():
    app.mount("/", StaticFiles(directory=str(_frontend_dir), html=True), name="frontend")


# Mount Socket.IO on /socket.io — wraps FastAPI so both REST and WebSocket share the same port
import socketio
from app.realtime.websocket import sio
app = socketio.ASGIApp(sio, other_asgi_app=app)
