#!/bin/bash
# Launchpad Call Center - Monitoring Deployment Script

set -e

NAMESPACE="monitoring"
CONTEXT="${KUBE_CONTEXT:-default}"

echo "=== Launchpad Call Center - Monitoring Deployment ==="
echo ""

# Create monitoring namespace
echo "Creating monitoring namespace..."
kubectl create namespace $NAMESPACE --context="$CONTEXT" 2>/dev/null || echo "Namespace already exists"

# Deploy Prometheus
echo ""
echo "Deploying Prometheus..."
kubectl apply -f infrastructure/monitoring/prometheus/prometheus.yaml --context="$CONTEXT"
kubectl apply -f infrastructure/monitoring/prometheus/alerts-enhanced.yaml --context="$CONTEXT"

# Deploy Grafana
echo ""
echo "Deploying Grafana..."
kubectl apply -f infrastructure/monitoring/grafana/dashboards/api.json --context="$CONTEXT"

# Deploy ELK Stack
echo ""
echo "Deploying ELK Stack..."
kubectl apply -f infrastructure/monitoring/elk/elasticsearch.yaml --context="$CONTEXT"
kubectl apply -f infrastructure/monitoring/elk/kibana.yaml --context="$CONTEXT"
kubectl apply -f infrastructure/monitoring/elk/filebeat.yaml --context="$CONTEXT"

# Wait for pods
echo ""
echo "Waiting for monitoring pods..."
kubectl wait --for=condition=ready pod -l app=prometheus -n $NAMESPACE --timeout=120s --context="$CONTEXT" 2>/dev/null || echo "Prometheus pod not ready"
kubectl wait --for=condition=ready pod -l app=grafana -n $NAMESPACE --timeout=120s --context="$CONTEXT" 2>/dev/null || echo "Grafana pod not ready"
kubectl wait --for=condition=ready pod -l app=elasticsearch -n $NAMESPACE --timeout=180s --context="$CONTEXT" 2>/dev/null || echo "Elasticsearch pod not ready"

# Verify
echo ""
echo "=== Monitoring Status ==="
echo ""
echo "Pods:"
kubectl get pods -n $NAMESPACE --context="$CONTEXT"

echo ""
echo "Services:"
kubectl get services -n $NAMESPACE --context="$CONTEXT"

echo ""
echo "=== Monitoring URLs ==="
echo "Prometheus: http://prometheus.monitoring.svc:9090"
echo "Grafana: http://grafana.monitoring.svc:3000"
echo "Kibana: http://kibana.monitoring.svc:5601"
echo ""
echo "=== Deployment Complete ==="
