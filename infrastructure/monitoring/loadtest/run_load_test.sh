#!/bin/bash

# Load Test Runner
# Simulates 100K SMS/day and thousands of concurrent bookings

set -e

HOST=${1:-"http://localhost:8000"}
USERS=${2:-100}
SPAWN_RATE=${3:-10}
DURATION=${4:-"5m"}

echo "=== Launchpad Load Test ==="
echo "Host: $HOST"
echo "Users: $USERS"
echo "Spawn Rate: $SPAWN_RATE"
echo "Duration: $DURATION"
echo ""

# Install locust if not installed
if ! command -v locust &> /dev/null; then
    echo "Installing locust..."
    pip install locust
fi

# Run load test
echo "Starting load test..."
locust \
    -f locustfile.py \
    --host=$HOST \
    --users=$USERS \
    --spawn-rate=$SPAWN_RATE \
    --run-time=$DURATION \
    --headless \
    --csv=results \
    --html=report.html

echo ""
echo "=== Load Test Results ==="
echo "Results saved to results_stats.csv"
echo "HTML report saved to report.html"

# Display summary
if [ -f "results_stats.csv" ]; then
    echo ""
    echo "=== Summary ==="
    tail -n +2 results_stats.csv | head -20
fi
