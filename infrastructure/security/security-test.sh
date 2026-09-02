#!/bin/bash
# Security Testing Script (Step 24.4)
# Runs automated security scans against the API

set -e

# Configuration
API_URL="${1:-http://localhost:8000}"
ZAP_PORT=8090
REPORT_DIR="./security-reports"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

echo "=== Launchpad Security Testing ==="
echo "Target: $API_URL"
echo "Report: $REPORT_DIR/security-report-$TIMESTAMP.html"
echo ""

# Create report directory
mkdir -p "$REPORT_DIR"

# Function: Check if ZAP is running
check_zap() {
    if curl -s "http://localhost:$ZAP_PORT" > /dev/null 2>&1; then
        return 0
    fi
    return 1
}

# Function: Start ZAP
start_zap() {
    echo "Starting OWASP ZAP..."
    docker run -d \
        --name zap \
        -p $ZAP_PORT:8080 \
        -i owasp/zap2docker-stable \
        zap.sh -daemon -host 0.0.0.0 -port 8080

    # Wait for ZAP to start
    echo "Waiting for ZAP to start..."
    for i in {1..30}; do
        if check_zap; then
            echo "ZAP is ready"
            return 0
        fi
        sleep 2
    done

    echo "Error: ZAP failed to start"
    return 1
}

# Function: Run API scan
run_api_scan() {
    echo "Running API scan..."

    # Spider the API
    echo "Spidering API..."
    curl -s "http://localhost:$ZAP_PORT/JSON/spider/action/scan/?url=$API_URL&maxChildren=20&recurse=true"

    # Wait for spider to complete
    sleep 30

    # Run active scan
    echo "Running active scan..."
    curl -s "http://localhost:$ZAP_PORT/JSON/ascan/action/scan/?url=$API_URL&recurse=true"

    # Wait for scan to complete
    echo "Waiting for scan to complete..."
    while true; do
        STATUS=$(curl -s "http://localhost:$ZAP_PORT/JSON/ascan/view/status/" | grep -o '"status":[0-9]*' | cut -d: -f2)
        if [ "$STATUS" = "100" ]; then
            break
        fi
        echo "Scan progress: $STATUS%"
        sleep 10
    done

    echo "Scan complete"
}

# Function: Generate report
generate_report() {
    echo "Generating report..."

    # Get HTML report
    curl -s "http://localhost:$ZAP_PORT/OTHER/core/other/htmlreport/" > "$REPORT_DIR/security-report-$TIMESTAMP.html"

    # Get JSON alerts
    curl -s "http://localhost:$ZAP_PORT/JSON/core/view/alerts/" > "$REPORT_DIR/alerts-$TIMESTAMP.json"

    echo "Report saved to $REPORT_DIR/security-report-$TIMESTAMP.html"
}

# Function: Run basic security checks
run_basic_checks() {
    echo "Running basic security checks..."

    # Check security headers
    echo "Checking security headers..."
    HEADERS=$(curl -sI "$API_URL/api/v1/health")

    if echo "$HEADERS" | grep -qi "x-content-type-options"; then
        echo "✓ X-Content-Type-Options present"
    else
        echo "✗ X-Content-Type-Options missing"
    fi

    if echo "$HEADERS" | grep -qi "x-frame-options"; then
        echo "✓ X-Frame-Options present"
    else
        echo "✗ X-Frame-Options missing"
    fi

    if echo "$HEADERS" | grep -qi "x-xss-protection"; then
        echo "✓ X-XSS-Protection present"
    else
        echo "✗ X-XSS-Protection missing"
    fi

    if echo "$HEADERS" | grep -qi "strict-transport-security"; then
        echo "✓ Strict-Transport-Security present"
    else
        echo "✗ Strict-Transport-Security missing"
    fi

    # Check for server header leakage
    if echo "$HEADERS" | grep -qi "server:"; then
        echo "✗ Server header present (information disclosure)"
    else
        echo "✓ Server header not present"
    fi

    # Check for error handling
    echo "Checking error handling..."
    ERROR_RESPONSE=$(curl -s "$API_URL/api/v1/nonexistent")
    if echo "$ERROR_RESPONSE" | grep -qi "traceback\|stack trace\|exception"; then
        echo "✗ Error response contains stack trace"
    else
        echo "✓ Error response does not contain stack trace"
    fi
}

# Function: Cleanup
cleanup() {
    echo "Cleaning up..."
    docker stop zap 2>/dev/null || true
    docker rm zap 2>/dev/null || true
}

# Main execution
main() {
    echo "Starting security tests..."

    # Run basic checks
    run_basic_checks

    # Try to run ZAP scan if available
    if command -v docker &> /dev/null; then
        start_zap
        run_api_scan
        generate_report
        cleanup
    else
        echo "Docker not available, skipping ZAP scan"
        echo "Run manually: docker run -it owasp/zap2docker-stable zap.sh -daemon"
    fi

    echo ""
    echo "=== Security Testing Complete ==="
}

# Run main function
main
