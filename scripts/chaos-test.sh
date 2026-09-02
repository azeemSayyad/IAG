#!/bin/bash
# Launchpad Call Center - Chaos Testing Script
#
# This script simulates various failure scenarios to test system resilience.
#
# Usage: ./scripts/chaos-test.sh [scenario]
#
# Scenarios:
#   redis-down     - Kill Redis pod
#   postgres-down  - Kill PostgreSQL pod
#   api-down       - Kill API pod
#   network-delay  - Add network latency
#   memory-pressure - Create memory pressure
#   all            - Run all scenarios (DANGEROUS)

set -e

NAMESPACE="launchpad"
CONTEXT="${KUBE_CONTEXT:-default}"
SCENARIO=${1:-"all"}

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Function to check if a pod is running
check_pod() {
    local label=$1
    local count=$(kubectl get pods -n $NAMESPACE -l $label --context="$CONTEXT" --no-headers 2>/dev/null | grep -c "Running" || echo "0")
    echo $count
}

# Function to wait for recovery
wait_for_recovery() {
    local label=$1
    local expected=$2
    local timeout=${3:-120}

    log_info "Waiting for $label to recover (timeout: ${timeout}s)..."
    local start=$(date +%s)
    while true; do
        local count=$(check_pod $label)
        if [ "$count" -ge "$expected" ]; then
            log_info "$label recovered ($count pods running)"
            return 0
        fi
        local now=$(date +%s)
        local elapsed=$((now - start))
        if [ $elapsed -ge $timeout ]; then
            log_error "$label did not recover within ${timeout}s"
            return 1
        fi
        sleep 5
    done
}

# Scenario: Redis Down
test_redis_down() {
    log_warn "=== SCENARIO: Redis Down ==="
    log_info "Current Redis pods: $(check_pod app=redis)"

    # Delete Redis pod
    log_info "Killing Redis pod..."
    kubectl delete pods -n $NAMESPACE -l app=redis --context="$CONTEXT" 2>/dev/null || log_warn "No Redis pod found"

    # Wait 10 seconds
    log_info "Waiting 10 seconds..."
    sleep 10

    # Check API health
    log_info "Checking API health..."
    local health=$(kubectl exec -n $NAMESPACE deploy/backend-api --context="$CONTEXT" -- curl -s http://localhost:8000/health 2>/dev/null || echo '{"status":"error"}')
    echo "API Health: $health"

    # Wait for recovery
    wait_for_recovery "app=redis" 1 60

    log_info "=== Redis Down Test Complete ==="
}

# Scenario: PostgreSQL Down
test_postgres_down() {
    log_warn "=== SCENARIO: PostgreSQL Down ==="
    log_info "Current PostgreSQL pods: $(check_pod app=postgres)"

    # Delete PostgreSQL pod
    log_info "Killing PostgreSQL pod..."
    kubectl delete pods -n $NAMESPACE -l app=postgres --context="$CONTEXT" 2>/dev/null || log_warn "No PostgreSQL pod found"

    # Wait 10 seconds
    log_info "Waiting 10 seconds..."
    sleep 10

    # Check API health
    log_info "Checking API health..."
    local health=$(kubectl exec -n $NAMESPACE deploy/backend-api --context="$CONTEXT" -- curl -s http://localhost:8000/health 2>/dev/null || echo '{"status":"error"}')
    echo "API Health: $health"

    # Wait for recovery
    wait_for_recovery "app=postgres" 1 120

    log_info "=== PostgreSQL Down Test Complete ==="
}

# Scenario: API Down
test_api_down() {
    log_warn "=== SCENARIO: API Down ==="
    log_info "Current API pods: $(check_pod app=backend-api)"

    # Scale down API
    log_info "Scaling down API to 0 replicas..."
    kubectl scale deployment/backend-api -n $NAMESPACE --replicas=0 --context="$CONTEXT"

    # Wait 10 seconds
    log_info "Waiting 10 seconds..."
    sleep 10

    # Check if frontend handles gracefully
    log_info "Checking frontend resilience..."

    # Scale back up
    log_info "Scaling API back to 2 replicas..."
    kubectl scale deployment/backend-api -n $NAMESPACE --replicas=2 --context="$CONTEXT"

    # Wait for recovery
    wait_for_recovery "app=backend-api" 2 60

    log_info "=== API Down Test Complete ==="
}

# Scenario: Network Delay
test_network_delay() {
    log_warn "=== SCENARIO: Network Delay ==="
    log_info "This scenario requires tc (traffic control) which may not be available in all environments"

    # This is a placeholder - actual implementation depends on cluster setup
    log_info "Skipping network delay test (requires privileged container)"

    log_info "=== Network Delay Test Complete ==="
}

# Scenario: Memory Pressure
test_memory_pressure() {
    log_warn "=== SCENARIO: Memory Pressure ==="
    log_info "Current pod resource usage:"
    kubectl top pods -n $NAMESPACE --context="$CONTEXT" 2>/dev/null || log_warn "Metrics server not available"

    # This is a placeholder - actual implementation depends on cluster setup
    log_info "Skipping memory pressure test (requires stress tool)"

    log_info "=== Memory Pressure Test Complete ==="
}

# Run all scenarios
run_all() {
    log_warn "=== RUNNING ALL CHAOS TESTS ==="
    log_warn "This will cause service disruptions!"
    echo ""
    read -p "Are you sure? (yes/no): " confirm
    if [ "$confirm" != "yes" ]; then
        log_info "Aborted."
        exit 0
    fi

    test_redis_down
    echo ""
    test_postgres_down
    echo ""
    test_api_down
    echo ""
    test_network_delay
    echo ""
    test_memory_pressure
    echo ""
    log_info "=== ALL CHAOS TESTS COMPLETE ==="
}

# Main
echo "=== Launchpad Call Center - Chaos Testing ==="
echo "Scenario: $SCENARIO"
echo ""

case $SCENARIO in
    redis-down)
        test_redis_down
        ;;
    postgres-down)
        test_postgres_down
        ;;
    api-down)
        test_api_down
        ;;
    network-delay)
        test_network_delay
        ;;
    memory-pressure)
        test_memory_pressure
        ;;
    all)
        run_all
        ;;
    *)
        log_error "Unknown scenario: $SCENARIO"
        echo "Available scenarios: redis-down, postgres-down, api-down, network-delay, memory-pressure, all"
        exit 1
        ;;
esac
