#!/bin/bash
# LAUNCHPAD CALL CENTER - FINAL E2E TEST

BASE="http://localhost:8000/api/v1"
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'
PASS=0
FAIL=0

log_test() { echo -e "${BLUE}[TEST]${NC} $1"; }
log_pass() { echo -e "${GREEN}[PASS]${NC} $1"; PASS=$((PASS + 1)); }
log_fail() { echo -e "${RED}[FAIL]${NC} $1"; FAIL=$((FAIL + 1)); }
log_out() { echo -e "${YELLOW}[OUT]${NC} ${1:0:200}"; }

# Get token first
TOKEN=$(curl -s -X POST "$BASE/auth/login" -H "Content-Type: application/json" \
  -d '{"email":"e2e@test.com","password":"TestPass123!"}' | python3 -c "import sys,json;print(json.load(sys.stdin)['access_token'])")
REFRESH=$(curl -s -X POST "$BASE/auth/login" -H "Content-Type: application/json" \
  -d '{"email":"e2e@test.com","password":"TestPass123!"}' | python3 -c "import sys,json;print(json.load(sys.stdin)['refresh_token'])")

echo ""
echo "=========================================="
echo "  1. AUTHENTICATION"
echo "=========================================="

log_test "1.1 POST /auth/register"
R=$(curl -s -X POST "$BASE/auth/register" -H "Content-Type: application/json" \
  -d '{"email":"newuser@test.com","password":"TestPass123!","first_name":"New","last_name":"User","company_name":"TestCo"}')
log_out "$R"
echo "$R" | grep -q "access_token\|already registered" && log_pass "Register" || log_fail "Register: $R"

log_test "1.2 POST /auth/login"
R=$(curl -s -X POST "$BASE/auth/login" -H "Content-Type: application/json" \
  -d '{"email":"e2e@test.com","password":"TestPass123!"}')
log_out "$R"
echo "$R" | grep -q "access_token" && log_pass "Login" || log_fail "Login: $R"

log_test "1.3 GET /auth/me"
R=$(curl -s "$BASE/auth/me" -H "Authorization: Bearer $TOKEN")
log_out "$R"
echo "$R" | grep -q "e2e@test.com" && log_pass "Get me" || log_fail "Get me: $R"

log_test "1.4 POST /auth/refresh"
R=$(curl -s -X POST "$BASE/auth/refresh" -H "Content-Type: application/json" \
  -d "{\"refresh_token\":\"$REFRESH\"}")
log_out "$R"
echo "$R" | grep -q "access_token" && log_pass "Refresh" || log_fail "Refresh: $R"

echo ""
echo "=========================================="
echo "  2. LEAD MANAGEMENT"
echo "=========================================="

log_test "2.1 POST /leads"
R=$(curl -s -X POST "$BASE/leads" -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"source":"website","first_name":"Sarah","last_name":"Johnson","phone":"+15551234567","email":"sarah@test.com","state":"CA","city":"LA"}')
log_out "$R"
LEAD1=$(echo "$R" | python3 -c "import sys,json;print(json.load(sys.stdin)['id'])" 2>/dev/null)
[ -n "$LEAD1" ] && log_pass "Create lead (ID: $LEAD1)" || log_fail "Create lead: $R"

log_test "2.2 POST /leads (second)"
R=$(curl -s -X POST "$BASE/leads" -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"source":"referral","first_name":"Mike","last_name":"Williams","phone":"+15559876543","email":"mike@test.com","state":"TX"}')
log_out "$R"
echo "$R" | grep -q "id" && log_pass "Create second lead" || log_fail "Create second lead: $R"

log_test "2.3 GET /leads"
R=$(curl -s "$BASE/leads?page=1&size=10" -H "Authorization: Bearer $TOKEN")
log_out "$R"
echo "$R" | grep -q "items" && log_pass "List leads" || log_fail "List leads: $R"

log_test "2.4 GET /leads/{id}"
R=$(curl -s "$BASE/leads/$LEAD1" -H "Authorization: Bearer $TOKEN")
log_out "$R"
echo "$R" | grep -q "Sarah" && log_pass "Get lead" || log_fail "Get lead: $R"

log_test "2.5 PATCH /leads/{id}"
R=$(curl -s -X PATCH "$BASE/leads/$LEAD1" -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"status":"contacted","lead_score":75}')
log_out "$R"
echo "$R" | grep -q "contacted" && log_pass "Update lead" || log_fail "Update lead: $R"

echo ""
echo "=========================================="
echo "  3. CONVERSATIONS"
echo "=========================================="

log_test "3.1 POST /conversations"
R=$(curl -s -X POST "$BASE/conversations" -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d "{\"lead_id\":\"$LEAD1\",\"status\":\"initiated\"}")
log_out "$R"
CONV=$(echo "$R" | python3 -c "import sys,json;print(json.load(sys.stdin)['id'])" 2>/dev/null)
[ -n "$CONV" ] && log_pass "Create conversation (ID: $CONV)" || log_fail "Create conversation: $R"

log_test "3.2 POST /messages (customer)"
R=$(curl -s -X POST "$BASE/conversations/$CONV/messages" -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"sender":"customer","content":"I am interested in auto insurance","message_type":"sms"}')
log_out "$R"
echo "$R" | grep -q "id" && log_pass "Add customer message" || log_fail "Add customer message: $R"

log_test "3.3 POST /messages (ai)"
R=$(curl -s -X POST "$BASE/conversations/$CONV/messages" -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"sender":"ai","content":"Great! When works for a call?","message_type":"sms"}')
log_out "$R"
echo "$R" | grep -q "id" && log_pass "Add AI message" || log_fail "Add AI message: $R"

log_test "3.4 GET /messages"
R=$(curl -s "$BASE/conversations/$CONV/messages" -H "Authorization: Bearer $TOKEN")
log_out "$R"
echo "$R" | grep -q "items" && log_pass "Get messages" || log_fail "Get messages: $R"

log_test "3.5 PATCH /conversations"
R=$(curl -s -X PATCH "$BASE/conversations/$CONV" -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"status":"active","intent":"INTERESTED","sentiment":"positive"}')
log_out "$R"
echo "$R" | grep -q "active" && log_pass "Update conversation" || log_fail "Update conversation: $R"

echo ""
echo "=========================================="
echo "  4. INTENT DETECTION"
echo "=========================================="

log_test "4.1 POST /intent/detect (positive)"
R=$(curl -s -X POST "$BASE/intent/detect" -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"text":"Yes, I would love to schedule a call!"}')
log_out "$R"
echo "$R" | grep -q "POSITIVE" && log_pass "Intent positive" || log_fail "Intent positive: $R"

log_test "4.2 POST /intent/detect (stop)"
R=$(curl -s -X POST "$BASE/intent/detect" -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"text":"STOP"}')
log_out "$R"
echo "$R" | grep -q "STOP" && log_pass "Intent stop" || log_fail "Intent stop: $R"

log_test "4.3 GET /intent/classes"
R=$(curl -s "$BASE/intent/classes" -H "Authorization: Bearer $TOKEN")
log_out "$R"
echo "$R" | grep -q "intents" && log_pass "Get classes" || log_fail "Get classes: $R"

log_test "4.4 POST /intent/objection/handle"
R=$(curl -s -X POST "$BASE/intent/objection/handle" -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"text":"That is too expensive","first_name":"Sarah","tone":"friendly"}')
log_out "$R"
echo "$R" | grep -q "objection_type" && log_pass "Handle objection" || log_fail "Handle objection: $R"

echo ""
echo "=========================================="
echo "  5. BOOKING FLOW"
echo "=========================================="

TOMORROW=$(date -v+1d +%Y-%m-%d)
log_test "5.1 GET /booking/slots"
R=$(curl -s "$BASE/booking/slots?date=$TOMORROW" -H "Authorization: Bearer $TOKEN")
log_out "$R"
echo "$R" | grep -q "slots" && log_pass "Get slots" || log_fail "Get slots: $R"

log_test "5.2 POST /booking/start"
R=$(curl -s -X POST "$BASE/booking/start" -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d "{\"lead_id\":\"$LEAD1\",\"conversation_id\":\"$CONV\"}")
log_out "$R"
echo "$R" | grep -q "options\|slots\|message\|success" && log_pass "Start booking" || log_fail "Start booking: $R"

log_test "5.3 POST /booking/reminders/process"
R=$(curl -s -X POST "$BASE/booking/reminders/process" -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{}')
log_out "$R"
echo "$R" | grep -q "total_pending\|processed" && log_pass "Process reminders" || log_fail "Process reminders: $R"

echo ""
echo "=========================================="
echo "  6. FOLLOWUP FLOW"
echo "=========================================="

log_test "6.1 POST /followup/no-reply/process"
R=$(curl -s -X POST "$BASE/followup/no-reply/process" -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{}')
log_out "$R"
echo "$R" | grep -q "total_checked\|processed" && log_pass "No-reply" || log_fail "No-reply: $R"

log_test "6.2 POST /followup/missed/process"
R=$(curl -s -X POST "$BASE/followup/missed/process" -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{}')
log_out "$R"
echo "$R" | grep -q "total_checked\|processed" && log_pass "Missed" || log_fail "Missed: $R"

log_test "6.3 POST /followup/nurture/process"
R=$(curl -s -X POST "$BASE/followup/nurture/process" -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{}')
log_out "$R"
echo "$R" | grep -q "total_checked\|processed" && log_pass "Nurture" || log_fail "Nurture: $R"

echo ""
echo "=========================================="
echo "  7. CAMPAIGNS"
echo "=========================================="

log_test "7.1 POST /admin/campaigns"
R=$(curl -s -X POST "$BASE/admin/campaigns" -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"name":"E2E Campaign","description":"Test","tone":"friendly","status":"active","booking_enabled":true}')
log_out "$R"
echo "$R" | grep -q "id" && log_pass "Create campaign" || log_fail "Create campaign: $R"

log_test "7.2 GET /admin/campaigns"
R=$(curl -s "$BASE/admin/campaigns" -H "Authorization: Bearer $TOKEN")
log_out "$R"
echo "$R" | grep -q "campaigns" && log_pass "List campaigns" || log_fail "List campaigns: $R"

echo ""
echo "=========================================="
echo "  8. ANALYTICS"
echo "=========================================="

log_test "8.1 GET /admin/analytics/overview"
R=$(curl -s "$BASE/admin/analytics/overview" -H "Authorization: Bearer $TOKEN")
log_out "$R"
echo "$R" | grep -q "total_leads" && log_pass "Analytics overview" || log_fail "Analytics overview: $R"

log_test "8.2 GET /admin/analytics/trends"
R=$(curl -s "$BASE/admin/analytics/trends?days=7" -H "Authorization: Bearer $TOKEN")
log_out "$R"
echo "$R" | grep -q "trends" && log_pass "Analytics trends" || log_fail "Analytics trends: $R"

echo ""
echo "=========================================="
echo "  9. AGENT OS"
echo "=========================================="

log_test "9.1 GET /agent/dispositions"
R=$(curl -s "$BASE/agent/dispositions" -H "Authorization: Bearer $TOKEN")
log_out "$R"
echo "$R" | grep -q "dispositions" && log_pass "Get dispositions" || log_fail "Get dispositions: $R"

echo ""
echo "=========================================="
echo "  10. SECURITY"
echo "=========================================="

log_test "10.1 GET /security/rate-limit/status"
R=$(curl -s "$BASE/security/rate-limit/status" -H "Authorization: Bearer $TOKEN")
log_out "$R"
echo "$R" | grep -q "limit\|remaining" && log_pass "Rate limit" || log_fail "Rate limit: $R"

log_test "10.2 GET /security/suppression"
R=$(curl -s "$BASE/security/suppression" -H "Authorization: Bearer $TOKEN")
log_out "$R"
echo "$R" | grep -q "phones\|total" && log_pass "Suppression" || log_fail "Suppression: $R"

echo ""
echo "=========================================="
echo "  11. ML PREDICTIONS"
echo "=========================================="

log_test "11.1 GET /ml/predict/{lead_id}"
R=$(curl -s "$BASE/ml/predict/$LEAD1" -H "Authorization: Bearer $TOKEN")
log_out "$R"
echo "$R" | grep -q "booking_probability" && log_pass "Predict" || log_fail "Predict: $R"

log_test "11.2 GET /ml/agents/ranking"
R=$(curl -s "$BASE/ml/agents/ranking" -H "Authorization: Bearer $TOKEN")
log_out "$R"
echo "$R" | grep -q "rankings" && log_pass "Agent ranking" || log_fail "Agent ranking: $R"

echo ""
echo "=========================================="
echo "  12. INGESTION"
echo "=========================================="

log_test "12.1 POST /ingestion/api"
R=$(curl -s -X POST "$BASE/ingestion/api" -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"source":"api","first_name":"Import","last_name":"Test","phone":"+15555556666","email":"import2@test.com"}')
log_out "$R"
echo "$R" | grep -q "id" && log_pass "API import" || log_fail "API import: $R"

echo ""
echo "=========================================="
echo "  13. AUDIT"
echo "=========================================="

log_test "13.1 GET /audit"
R=$(curl -s "$BASE/audit?page=1&size=10" -H "Authorization: Bearer $TOKEN")
log_out "$R"
echo "$R" | grep -q "items" && log_pass "Audit logs" || log_fail "Audit logs: $R"

echo ""
echo "=========================================="
echo "  14. REALTIME"
echo "=========================================="

log_test "14.1 GET /realtime/online"
R=$(curl -s "$BASE/realtime/online" -H "Authorization: Bearer $TOKEN")
log_out "$R"
echo "$R" | grep -q "online" && log_pass "Online users" || log_fail "Online users: $R"

log_test "14.2 GET /realtime/status"
R=$(curl -s "$BASE/realtime/status" -H "Authorization: Bearer $TOKEN")
log_out "$R"
echo "$R" | grep -q "status" && log_pass "Realtime status" || log_fail "Realtime status: $R"

echo ""
echo "=========================================="
echo "  SUMMARY"
echo "=========================================="
echo ""
echo -e "Passed: ${GREEN}$PASS${NC}"
echo -e "Failed: ${RED}$FAIL${NC}"
echo ""
[ $FAIL -eq 0 ] && echo -e "${GREEN}ALL TESTS PASSED!${NC}" || echo -e "${RED}SOME TESTS FAILED${NC}"
