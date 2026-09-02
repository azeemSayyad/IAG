#!/bin/bash
# Launchpad Call Center - Multi-Region Deployment Script (Phase 50)
#
# Deploys the entire platform across 3 AWS regions:
# - us-east-1 (Primary)
# - eu-west-1 (Europe)
# - ap-southeast-1 (Asia Pacific)
#
# Usage:
#   ./scripts/deploy-multi-region.sh [environment]
#   ./scripts/deploy-multi-region.sh production
#
# Prerequisites:
#   - AWS CLI configured with appropriate credentials
#   - Terraform installed (>= 1.5.0)
#   - Docker installed
#   - kubectl configured for each region

set -euo pipefail

# --- Configuration ---

ENVIRONMENT="${1:-production}"
PRIMARY_REGION="us-east-1"
EU_REGION="eu-west-1"
AP_REGION="ap-southeast-1"
PROJECT="launchpad"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INFRA_DIR="${SCRIPT_DIR}/../infrastructure/aws"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info()    { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn()    { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error()   { echo -e "${RED}[ERROR]${NC} $1"; }
log_section() { echo -e "\n${BLUE}=== $1 ===${NC}\n"; }

# --- Pre-flight Checks ---

log_section "Pre-flight Checks"

check_command() {
  if ! command -v "$1" &> /dev/null; then
    log_error "$1 is not installed"
    exit 1
  fi
  log_info "$1 found: $(command -v "$1")"
}

check_command aws
check_command terraform
check_command docker

# Verify AWS credentials
log_info "Verifying AWS credentials..."
aws sts get-caller-identity > /dev/null 2>&1 || {
  log_error "AWS credentials not configured"
  exit 1
}
log_info "AWS credentials valid"

# --- Step 1: Terraform Infrastructure ---

log_section "Step 1: Deploy Infrastructure (Terraform)"

deploy_terraform() {
  local region=$1
  log_info "Deploying infrastructure to ${region}..."

  cd "${INFRA_DIR}"

  # Initialize Terraform
  terraform init \
    -backend-config="key=${ENVIRONMENT}/${region}/terraform.tfstate" \
    -reconfigure

  # Plan
  terraform plan \
    -var="environment=${ENVIRONMENT}" \
    -var="region=${region}" \
    -out="tfplan-${region}"

  # Apply
  terraform apply \
    -auto-approve \
    "tfplan-${region}"

  log_info "Infrastructure deployed to ${region}"
}

# Deploy to all regions
deploy_terraform "${PRIMARY_REGION}"
deploy_terraform "${EU_REGION}"
deploy_terraform "${AP_REGION}"

# --- Step 2: Build and Push Docker Images ---

log_section "Step 2: Build and Push Docker Images"

build_and_push() {
  local region=$1
  local ecr_url

  log_info "Building and pushing to ${region}..."

  # Get ECR login
  aws ecr get-login-password --region "${region}" | \
    docker login --username AWS --password-stdin \
    "${AWS_ACCOUNT_ID}.dkr.ecr.${region}.amazonaws.com"

  # Get ECR repository URL
  ecr_url=$(aws ecr describe-repositories \
    --repository-names "${PROJECT}" \
    --region "${region}" \
    --query 'repositories[0].repositoryUri' \
    --output text)

  # Build backend image
  docker build \
    --platform linux/amd64 \
    -t "${ecr_url}:latest" \
    -t "${ecr_url}:$(git rev-parse --short HEAD)" \
    -f apps/backend-api/Dockerfile \
    apps/backend-api/

  # Push images
  docker push "${ecr_url}:latest"
  docker push "${ecr_url}:$(git rev-parse --short HEAD)"

  log_info "Images pushed to ${region}"
}

build_and_push "${PRIMARY_REGION}"
build_and_push "${EU_REGION}"
build_and_push "${AP_REGION}"

# --- Step 3: Deploy ECS Services ---

log_section "Step 3: Deploy ECS Services"

deploy_ecs() {
  local region=$1
  local cluster="${PROJECT}-cluster"

  log_info "Deploying ECS to ${region}..."

  # Update ECS service to use new task definition
  aws ecs update-service \
    --cluster "${cluster}" \
    --service "${PROJECT}-backend-service" \
    --force-new-deployment \
    --region "${region}"

  # Wait for service to stabilize
  log_info "Waiting for ECS service to stabilize in ${region}..."
  aws ecs wait services-stable \
    --cluster "${cluster}" \
    --services "${PROJECT}-backend-service" \
    --region "${region}" || {
    log_warn "ECS service may not be fully stable in ${region}"
  }

  log_info "ECS deployed to ${region}"
}

deploy_ecs "${PRIMARY_REGION}"
deploy_ecs "${EU_REGION}"
deploy_ecs "${AP_REGION}"

# --- Step 4: Deploy Frontend to S3 ---

log_section "Step 4: Deploy Frontend"

deploy_frontend() {
  log_info "Building frontend..."

  cd "${SCRIPT_DIR}/.."
  cd apps/frontend

  # Build
  npm ci
  npm run build

  # Upload to S3
  log_info "Uploading frontend to S3..."
  aws s3 sync \
    .next/static \
    "s3://launchpad-frontend-${ENVIRONMENT}/_next/static" \
    --cache-control "public, max-age=31536000, immutable" \
    --region "${PRIMARY_REGION}"

  aws s3 sync \
    out \
    "s3://launchpad-frontend-${ENVIRONMENT}" \
    --exclude "_next/static/*" \
    --cache-control "public, max-age=3600" \
    --region "${PRIMARY_REGION}"

  # Invalidate CloudFront cache
  log_info "Invalidating CloudFront cache..."
  local dist_id
  dist_id=$(aws cloudfront list-distributions \
    --query "DistributionList.Items[?Aliases.Items[?contains(@, 'app.launchpad.com')]].Id" \
    --output text)

  if [ -n "${dist_id}" ]; then
    aws cloudfront create-invalidation \
      --distribution-id "${dist_id}" \
      --paths "/*"
    log_info "CloudFront cache invalidated"
  fi

  log_info "Frontend deployed"
}

deploy_frontend

# --- Step 5: Run Database Migrations ---

log_section "Step 5: Run Database Migrations"

run_migrations() {
  log_info "Running database migrations on primary region..."

  # Get the primary ECS task ARN
  local task_arn
  task_arn=$(aws ecs run-task \
    --cluster "${PROJECT}-cluster" \
    --task-definition "${PROJECT}-migrations" \
    --network-configuration "awsvpcConfiguration={subnets=[${PRIVATE_SUBNET_IDS}],securityGroups=[${ECS_SECURITY_GROUP}],assignPublicIp=DISABLED}" \
    --region "${PRIMARY_REGION}" \
    --query 'tasks[0].taskArn' \
    --output text)

  # Wait for migration to complete
  aws ecs wait tasks-stopped \
    --cluster "${PROJECT}-cluster" \
    --tasks "${task_arn}" \
    --region "${PRIMARY_REGION}"

  log_info "Database migrations complete"
}

run_migrations

# --- Step 6: Verify Health ---

log_section "Step 6: Verify Health"

verify_health() {
  local region=$1
  local endpoint

  case "${region}" in
    "${PRIMARY_REGION}") endpoint="api-us.launchpad.com" ;;
    "${EU_REGION}")      endpoint="api-eu.launchpad.com" ;;
    "${AP_REGION}")      endpoint="api-ap.launchpad.com" ;;
  esac

  log_info "Checking health of ${endpoint}..."

  local retries=0
  local max_retries=10

  while [ ${retries} -lt ${max_retries} ]; do
    if curl -sf "https://${endpoint}/health" > /dev/null 2>&1; then
      log_info "${region} is healthy"
      return 0
    fi

    retries=$((retries + 1))
    log_warn "Health check failed, retrying (${retries}/${max_retries})..."
    sleep 10
  done

  log_error "${region} health check failed after ${max_retries} retries"
  return 1
}

verify_health "${PRIMARY_REGION}"
verify_health "${EU_REGION}"
verify_health "${AP_REGION}"

# --- Step 7: Verify DNS Routing ---

log_section "Step 7: Verify DNS Routing"

verify_dns() {
  log_info "Verifying DNS resolution for api.launchpad.com..."

  # Check that api.launchpad.com resolves
  if dig +short api.launchpad.com | head -1 | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$'; then
    log_info "DNS resolution successful"
  else
    log_warn "DNS may not have propagated yet"
  fi

  # Check health checks
  log_info "Route 53 health check status:"
  aws route53 get-health-check-status \
    --health-check-id "$(aws route53 list-health-checks \
      --query "HealthChecks[?HealthCheckConfig.FullyQualifiedDomainName=='api-us.launchpad.com'].Id" \
      --output text)" \
    --query 'StatusList' \
    --output table 2>/dev/null || log_warn "Could not retrieve health check status"
}

verify_dns

# --- Summary ---

log_section "Deployment Summary"

echo -e "
${GREEN}Multi-region deployment complete!${NC}

Regions deployed:
  - ${PRIMARY_REGION} (Primary) - All services
  - ${EU_REGION} (Europe) - API + Workers
  - ${AP_REGION} (Asia Pacific) - API + Workers

Endpoints:
  - API:        https://api.launchpad.com (latency-based routing)
  - Frontend:   https://app.launchpad.com (CloudFront CDN)
  - WebSocket:  wss://ws.launchpad.com (latency-based routing)

Direct regional access:
  - US: https://api-us.launchpad.com
  - EU: https://api-eu.launchpad.com
  - AP: https://api-ap.launchpad.com

Database:
  - Aurora Global Cluster with cross-region replication
  - Primary: ${PRIMARY_REGION} (writer + 1 reader)
  - Secondary: ${EU_REGION}, ${AP_REGION} (read replicas)

Disaster Recovery:
  - Automatic failover via Route 53 health checks
  - S3 cross-region replication for backups
  - Aurora backtrack enabled (72 hours)

To trigger manual failover:
  ./scripts/disaster-recovery.sh failover eu-west-1
"
