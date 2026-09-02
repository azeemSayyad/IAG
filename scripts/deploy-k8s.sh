#!/bin/bash
# Launchpad Call Center - Kubernetes Deployment Script

set -e

# Configuration
NAMESPACE="launchpad"
CONTEXT="${KUBE_CONTEXT:-default}"

echo "=== Launchpad Call Center - Kubernetes Deployment ==="
echo "Namespace: $NAMESPACE"
echo "Context: $CONTEXT"
echo ""

# Function to apply YAML with error handling
apply_yaml() {
    local file=$1
    local name=$2
    echo "Applying $name..."
    if kubectl apply -f "$file" --context="$CONTEXT" 2>/dev/null; then
        echo "  ✓ $name applied successfully"
    else
        echo "  ✗ Failed to apply $name"
        return 1
    fi
}

# Step 1: Create namespace
echo "Step 1: Creating namespace..."
apply_yaml "infrastructure/kubernetes/namespace.yaml" "Namespace"

# Step 2: Apply base configurations
echo ""
echo "Step 2: Applying base configurations..."
apply_yaml "infrastructure/kubernetes/base/configmap.yaml" "ConfigMap"
apply_yaml "infrastructure/kubernetes/base/secrets.yaml" "Secrets"

# Step 3: Deploy infrastructure
echo ""
echo "Step 3: Deploying infrastructure..."
apply_yaml "infrastructure/kubernetes/infrastructure/postgres.yaml" "PostgreSQL"
apply_yaml "infrastructure/kubernetes/infrastructure/redis.yaml" "Redis"
apply_yaml "infrastructure/kubernetes/infrastructure/clickhouse.yaml" "ClickHouse"
apply_yaml "infrastructure/kubernetes/infrastructure/qdrant.yaml" "Qdrant"

# Step 4: Wait for infrastructure to be ready
echo ""
echo "Step 4: Waiting for infrastructure pods..."
echo "  Waiting for PostgreSQL..."
kubectl wait --for=condition=ready pod -l app=postgres -n $NAMESPACE --timeout=120s --context="$CONTEXT" 2>/dev/null || echo "  ⚠ PostgreSQL pod not ready (may already be running)"

echo "  Waiting for Redis..."
kubectl wait --for=condition=ready pod -l app=redis -n $NAMESPACE --timeout=60s --context="$CONTEXT" 2>/dev/null || echo "  ⚠ Redis pod not ready (may already be running)"

# Step 5: Deploy application services
echo ""
echo "Step 5: Deploying application services..."
apply_yaml "infrastructure/kubernetes/services/backend-api.yaml" "Backend API"
apply_yaml "infrastructure/kubernetes/services/frontend.yaml" "Frontend"

# Step 6: Deploy additional services
echo ""
echo "Step 6: Deploying additional services..."
apply_yaml "infrastructure/kubernetes/services/booking-service.yaml" "Booking Service"
apply_yaml "infrastructure/kubernetes/services/ai-service.yaml" "AI Service"
apply_yaml "infrastructure/kubernetes/services/messaging-service.yaml" "Messaging Service"
apply_yaml "infrastructure/kubernetes/services/analytics-service.yaml" "Analytics Service"
apply_yaml "infrastructure/kubernetes/services/notification-service.yaml" "Notification Service"

# Step 7: Apply autoscaling
echo ""
echo "Step 7: Applying autoscaling..."
apply_yaml "infrastructure/kubernetes/services/hpa.yaml" "HPA"

# Step 8: Apply ingress
echo ""
echo "Step 8: Applying ingress..."
apply_yaml "infrastructure/kubernetes/services/ingress.yaml" "Ingress"

# Step 9: Verify deployment
echo ""
echo "Step 9: Verifying deployment..."
echo ""
echo "Pods:"
kubectl get pods -n $NAMESPACE --context="$CONTEXT"

echo ""
echo "Services:"
kubectl get services -n $NAMESPACE --context="$CONTEXT"

echo ""
echo "Ingress:"
kubectl get ingress -n $NAMESPACE --context="$CONTEXT"

echo ""
echo "=== Deployment Complete ==="
