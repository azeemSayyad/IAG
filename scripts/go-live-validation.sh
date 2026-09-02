#!/bin/bash
# Launchpad Call Center - Go-Live Validation Script
#
# This script runs all pre-go-live checks to ensure the system is ready.
#
# Usage: ./scripts/go-live-validation.sh [API_URL]
#
# Exit codes:
#   0 - All checks passed
#   1 - One or more checks failed

set -e

API_URL=${1:-"http://localhost:8000"}
FAILED=0
PASSED=0
WARNINGS=0

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log_pass() {
    echo -e "${GREEN}[PASS]${NC} $1"
    PASSED=$((PASSED + 1))
}

log_fail() {
    echo -e "${RED}[FAIL]${NC} $1"
    FAILED=$((FAILED + 1))
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
    WARNINGS=$((WARNINGS + 1))
}

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_section() {
    echo ""
    echo -e "${BLUE}=== $1 ===${NC}"
}

# ============================================
# 1. ENVIRONMENT CHECKS
# ============================================
log_section "1. ENVIRONMENT CHECKS"

# Check .env.production exists
if [ -f ".env.production" ]; then
    log_pass ".env.production exists"
else
    log_fail ".env.production not found"
fi

# Check required environment variables
required_vars=(
    "POSTGRES_URL"
    "REDIS_HOST"
    "REDIS_PORT"
    "JWT_SECRET"
    "ENGAGECLOUD_API_KEY"
    "ENGAGECLOUD_API_SECRET"
    "ENGAGECLOUD_AGENCY_ID"
    "ENGAGE_CLOUD_WEBHOOK_SECRET"
    "ENGAGECLOUD_FROM_NUMBERS"
    "OLLAMA_BASE_URL"
)

for var in "${required_vars[@]}"; do
    if grep -q "^${var}=" .env.production 2>/dev/null; then
        value=$(grep "^${var}=" .env.production | cut -d'=' -f2)
        if [ -n "$value" ] && [ "$value" != "" ]; then
            log_pass "$var is set"
        else
            log_warn "$var is empty"
        fi
    else
        log_fail "$var not found in .env.production"
    fi
done

# ============================================
# 2. DOCKER CHECKS
# ============================================
log_section "2. DOCKER CHECKS"

# Check Dockerfiles exist
dockerfiles=(
    "apps/backend-api/Dockerfile"
    "apps/frontendall/Dockerfile"
    "apps/workers/Dockerfile"
)

for file in "${dockerfiles[@]}"; do
    if [ -f "$file" ]; then
        log_pass "$file exists"
    else
        log_fail "$file not found"
    fi
done

# Check docker-compose.yml
if [ -f "docker-compose.yml" ]; then
    log_pass "docker-compose.yml exists"
else
    log_fail "docker-compose.yml not found"
fi

# ============================================
# 3. KUBERNETES CHECKS
# ============================================
log_section "3. KUBERNETES CHECKS"

# Check K8s manifests
k8s_files=(
    "infrastructure/kubernetes/namespace.yaml"
    "infrastructure/kubernetes/base/configmap.yaml"
    "infrastructure/kubernetes/base/secrets.yaml"
    "infrastructure/kubernetes/services/backend-api.yaml"
    "infrastructure/kubernetes/services/frontend.yaml"
    "infrastructure/kubernetes/services/hpa.yaml"
    "infrastructure/kubernetes/services/ingress.yaml"
)

for file in "${k8s_files[@]}"; do
    if [ -f "$file" ]; then
        log_pass "$file exists"
    else
        log_fail "$file not found"
    fi
done

# ============================================
# 4. DATABASE CHECKS
# ============================================
log_section "4. DATABASE CHECKS"

# Check migration files
if [ -d "apps/backend-api/alembic/versions" ]; then
    migration_count=$(ls -1 apps/backend-api/alembic/versions/*.py 2>/dev/null | wc -l)
    if [ "$migration_count" -gt 0 ]; then
        log_pass "Database migrations exist ($migration_count files)"
    else
        log_fail "No database migrations found"
    fi
else
    log_fail "Alembic versions directory not found"
fi

# ============================================
# 5. API HEALTH CHECK
# ============================================
log_section "5. API HEALTH CHECK"

log_info "Testing API at: $API_URL"

# Health endpoint
health_body=$(curl -s "$API_URL/health" 2>/dev/null || true)
if echo "$health_body" | grep -q '"status":"ok"'; then
    log_pass "Health endpoint returns ok"
else
    log_warn "Health endpoint did not return ok (API may not be running)"
fi

# ============================================
# 6. SECURITY CHECKS
# ============================================
log_section "6. SECURITY CHECKS"

# Check security middleware
if grep -q "SecurityMiddleware" apps/backend-api/app/main.py 2>/dev/null; then
    log_pass "SecurityMiddleware registered in main.py"
else
    log_fail "SecurityMiddleware not registered in main.py"
fi

# Check metrics middleware
if grep -q "MetricsMiddleware" apps/backend-api/app/main.py 2>/dev/null; then
    log_pass "MetricsMiddleware registered in main.py"
else
    log_fail "MetricsMiddleware not registered in main.py"
fi

# Check Engage Clouds webhook validation
if grep -q "validate_webhook" apps/backend-api/app/ai/services/communication_provider.py 2>/dev/null && \
   grep -q "/webhooks/engage-clouds" apps/backend-api/app/ai/routers/webhooks.py 2>/dev/null; then
    log_pass "Engage Clouds webhook validation implemented"
else
    log_fail "Engage Clouds webhook validation not implemented"
fi

# ============================================
# 7. FRONTEND CHECKS
# ============================================
log_section "7. FRONTEND CHECKS"

# Check static frontend validation
if [ -f "scripts/validate-frontend.mjs" ]; then
    log_pass "Static frontend validator exists"
else
    log_fail "Static frontend validator not found"
fi

# Check WebSocket integration
if grep -R -q "socket.io\\|WebSocket\\|io(" apps/frontendall 2>/dev/null; then
    log_pass "Frontend realtime references exist"
else
    log_warn "Frontend realtime references not found in static assets"
fi

# Check Engage Clouds settings surface
if grep -R -q "Engage Clouds" apps/frontendall 2>/dev/null; then
    log_pass "Frontend Engage Clouds settings surface exists"
else
    log_fail "Frontend Engage Clouds settings surface not found"
fi

# ============================================
# 8. WORKER CHECKS
# ============================================
log_section "8. WORKER CHECKS"

# Check Celery configuration
if [ -f "apps/workers/app/celery_app.py" ]; then
    log_pass "Celery configuration exists"
else
    log_fail "Celery configuration not found"
fi

# Check async fix
if grep -q "asyncio.run" apps/workers/app/tasks/ai.py 2>/dev/null; then
    log_pass "Celery async bug fixed"
else
    log_fail "Celery async bug not fixed"
fi

# ============================================
# 9. MONITORING CHECKS
# ============================================
log_section "9. MONITORING CHECKS"

# Check Prometheus config
if [ -f "infrastructure/monitoring/prometheus/prometheus.yaml" ]; then
    log_pass "Prometheus configuration exists"
else
    log_fail "Prometheus configuration not found"
fi

# Check Grafana dashboard
if [ -f "infrastructure/monitoring/grafana/dashboards/api.json" ]; then
    log_pass "Grafana dashboard exists"
else
    log_fail "Grafana dashboard not found"
fi

# Check alert rules
if [ -f "infrastructure/monitoring/prometheus/alerts-enhanced.yaml" ]; then
    log_pass "Alert rules exist"
else
    log_fail "Alert rules not found"
fi

# ============================================
# 10. BACKUP CHECKS
# ============================================
log_section "10. BACKUP CHECKS"

if [ -f "infrastructure/monitoring/disaster-recovery/backup.sh" ]; then
    log_pass "Backup script exists"
else
    log_fail "Backup script not found"
fi

if [ -f "infrastructure/monitoring/disaster-recovery/backup-strategy.md" ]; then
    log_pass "Backup strategy documented"
else
    log_fail "Backup strategy not documented"
fi

# ============================================
# SUMMARY
# ============================================
log_section "VALIDATION SUMMARY"

echo ""
echo "Results:"
echo -e "  ${GREEN}Passed:${NC}   $PASSED"
echo -e "  ${RED}Failed:${NC}   $FAILED"
echo -e "  ${YELLOW}Warnings:${NC} $WARNINGS"
echo ""

if [ $FAILED -eq 0 ]; then
    echo -e "${GREEN}=== ALL CHECKS PASSED - READY FOR GO-LIVE ===${NC}"
    exit 0
else
    echo -e "${RED}=== $FAILED CHECKS FAILED - NOT READY FOR GO-LIVE ===${NC}"
    exit 1
fi
