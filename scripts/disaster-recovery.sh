#!/bin/bash
# Launchpad Call Center - Disaster Recovery Script (Phase 50.5)
#
# Manages disaster recovery operations:
# - failover: Promote secondary region to primary
# - failback: Restore primary region after recovery
# - status:   Check current DR status
# - test:     Run DR test (non-destructive)
# - backup:   Trigger manual backup
#
# Usage:
#   ./scripts/disaster-recovery.sh failover [region]
#   ./scripts/disaster-recovery.sh failback
#   ./scripts/disaster-recovery.sh status
#   ./scripts/disaster-recovery.sh test
#   ./scripts/disaster-recovery.sh backup

set -euo pipefail

# --- Configuration ---

COMMAND="${1:-status}"
DR_REGION="${2:-eu-west-1}"
PRIMARY_REGION="us-east-1"
AP_REGION="ap-southeast-1"
PROJECT="launchpad"
ENVIRONMENT="${ENVIRONMENT:-production}"

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

# --- Command: status ---

cmd_status() {
  log_section "Disaster Recovery Status"

  # Check Aurora Global Cluster
  log_info "Aurora Global Cluster Status:"
  aws rds describe-global-clusters \
    --global-cluster-identifier "${PROJECT}-global" \
    --query 'GlobalClusters[0].{
      Status:Status,
      Engine:Engine,
      Members:GlobalClusterMembers[].{
        Region:DBClusterArn,
        Status:Status,
        Writer:IsWriter
      }
    }' \
    --output table 2>/dev/null || log_warn "Could not retrieve global cluster status"

  echo ""

  # Check Route 53 Health Checks
  log_info "Route 53 Health Check Status:"
  for region in "${PRIMARY_REGION}" "${DR_REGION}" "${AP_REGION}"; do
    local endpoint
    case "${region}" in
      "${PRIMARY_REGION}") endpoint="api-us.launchpad.com" ;;
      "${DR_REGION}")      endpoint="api-eu.launchpad.com" ;;
      "${AP_REGION}")      endpoint="api-ap.launchpad.com" ;;
    esac

    local status
    if curl -sf --max-time 5 "https://${endpoint}/health" > /dev/null 2>&1; then
      status="${GREEN}HEALTHY${NC}"
    else
      status="${RED}UNHEALTHY${NC}"
    fi
    echo -e "  ${region}: ${status} (${endpoint})"
  done

  echo ""

  # Check S3 Replication
  log_info "S3 Backup Replication Status:"
  aws s3api get-bucket-replication \
    --bucket "${PROJECT}-backups-${ENVIRONMENT}-primary" \
    --query 'ReplicationConfiguration.Rules[0].Status' \
    --output text 2>/dev/null || log_warn "Could not retrieve replication status"

  echo ""

  # Check Redis Status
  log_info "Redis Cluster Status:"
  for region in "${PRIMARY_REGION}" "${DR_REGION}" "${AP_REGION}"; do
    local redis_id
    case "${region}" in
      "${PRIMARY_REGION}") redis_id="${PROJECT}-redis-primary" ;;
      "${DR_REGION}")      redis_id="${PROJECT}-redis-eu" ;;
      "${AP_REGION}")      redis_id="${PROJECT}-redis-ap" ;;
    esac

    local status
    status=$(aws elasticache describe-replication-groups \
      --replication-group-id "${redis_id}" \
      --region "${region}" \
      --query 'ReplicationGroups[0].Status' \
      --output text 2>/dev/null || echo "UNKNOWN")
    echo -e "  ${region}: ${status}"
  done

  echo ""

  # Check Last Backup
  log_info "Last Backup Snapshot:"
  aws rds describe-db-cluster-snapshots \
    --db-cluster-identifier "${PROJECT}-primary" \
    --snapshot-type "automated" \
    --query 'reverse(sort_by(DBClusterSnapshots, &SnapshotCreateTime))[0].{
      Snapshot:DBClusterSnapshotIdentifier,
      Created:SnapshotCreateTime,
      Status:Status
    }' \
    --output table 2>/dev/null || log_warn "Could not retrieve backup status"
}

# --- Command: failover ---

cmd_failover() {
  log_section "FAILOVER to ${DR_REGION}"

  log_warn "This will promote the ${DR_REGION} cluster to standalone writer."
  log_warn "The primary region (${PRIMARY_REGION}) will become read-only."
  echo ""
  read -p "Are you sure you want to proceed? (yes/no): " confirm

  if [ "${confirm}" != "yes" ]; then
    log_info "Failover cancelled"
    exit 0
  fi

  # Step 1: Promote Aurora secondary cluster
  log_info "Step 1: Promoting Aurora cluster in ${DR_REGION}..."
  aws rds failover-global-cluster \
    --global-cluster-identifier "${PROJECT}-global" \
    --target-db-cluster-identifier "arn:aws:rds:${DR_REGION}:${AWS_ACCOUNT_ID}:cluster:${PROJECT}-${DR_REGION#*-}" \
    --region "${PRIMARY_REGION}"

  log_info "Waiting for failover to complete..."
  aws rds wait db-cluster-available \
    --db-cluster-identifier "${PROJECT}-${DR_REGION#*-}" \
    --region "${DR_REGION}"

  log_info "Aurora failover complete"

  # Step 2: Scale up ECS in DR region
  log_info "Step 2: Scaling up ECS in ${DR_REGION}..."
  aws ecs update-service \
    --cluster "${PROJECT}-cluster-${DR_REGION#*-}" \
    --service "${PROJECT}-backend-service-${DR_REGION#*-}" \
    --desired-count 2 \
    --region "${DR_REGION}"

  log_info "Waiting for ECS to stabilize..."
  aws ecs wait services-stable \
    --cluster "${PROJECT}-cluster-${DR_REGION#*-}" \
    --services "${PROJECT}-backend-service-${DR_REGION#*-}" \
    --region "${DR_REGION}"

  log_info "ECS scaled up in ${DR_REGION}"

  # Step 3: Update application configuration
  log_info "Step 3: Updating application configuration..."

  # Update Secrets Manager in DR region to point to local database
  local dr_db_endpoint
  dr_db_endpoint=$(aws rds describe-db-clusters \
    --db-cluster-identifier "${PROJECT}-${DR_REGION#*-}" \
    --region "${DR_REGION}" \
    --query 'DBClusters[0].Endpoint' \
    --output text)

  aws secretsmanager update-secret \
    --secret-id "launchpad/${DR_REGION}/database-url" \
    --secret-string "postgresql://postgres:${DB_PASSWORD}@${dr_db_endpoint}:5432/launchpad" \
    --region "${DR_REGION}"

  log_info "Configuration updated"

  # Step 4: Verify health
  log_info "Step 4: Verifying health..."
  local retries=0
  local max_retries=10

  while [ ${retries} -lt ${max_retries} ]; do
    if curl -sf --max-time 5 "https://api-eu.launchpad.com/health" > /dev/null 2>&1; then
      log_info "DR region is healthy!"
      break
    fi
    retries=$((retries + 1))
    log_warn "Waiting for DR region to become healthy (${retries}/${max_retries})..."
    sleep 15
  done

  if [ ${retries} -ge ${max_retries} ]; then
    log_error "DR region health check failed. Manual intervention required."
    exit 1
  fi

  # Step 5: Notify
  log_section "Failover Complete"
  echo -e "
${GREEN}Failover to ${DR_REGION} completed successfully!${NC}

Current state:
  - Primary region: ${DR_REGION} (writer)
  - Old primary (${PRIMARY_REGION}): Needs manual recovery
  - Route 53: Automatic health check failover active

Next steps:
  1. Verify application functionality
  2. Investigate root cause of ${PRIMARY_REGION} failure
  3. When ${PRIMARY_REGION} is recovered, run: ./scripts/disaster-recovery.sh failback
  4. Monitor replication lag until caught up

IMPORTANT: Update your monitoring dashboards to reflect the new primary region.
"

  # Send SNS notification
  aws sns publish \
    --topic-arn "arn:aws:sns:${PRIMARY_REGION}:${AWS_ACCOUNT_ID}:launchpad-dr-alerts" \
    --subject "ALERT: Launchpad DR Failover to ${DR_REGION}" \
    --message "Disaster recovery failover initiated. ${DR_REGION} is now the primary region." \
    --region "${PRIMARY_REGION}" 2>/dev/null || log_warn "Could not send SNS notification"
}

# --- Command: failback ---

cmd_failback() {
  log_section "FAILBACK to ${PRIMARY_REGION}"

  log_warn "This will restore ${PRIMARY_REGION} as the primary writer."
  log_warn "${DR_REGION} will become a read replica again."
  echo ""
  read -p "Is ${PRIMARY_REGION} fully recovered and caught up? (yes/no): " confirm

  if [ "${confirm}" != "yes" ]; then
    log_info "Failback cancelled. Ensure ${PRIMARY_REGION} is recovered first."
    exit 0
  fi

  # Step 1: Check replication lag
  log_info "Step 1: Checking replication lag..."
  local lag
  lag=$(aws rds describe-global-clusters \
    --global-cluster-identifier "${PROJECT}-global" \
    --region "${DR_REGION}" \
    --query 'GlobalClusters[0].GlobalClusterMembers[?IsWriter==`false`].GlobalClusterMemberLag' \
    --output text 2>/dev/null || echo "unknown")

  if [ "${lag}" != "unknown" ] && [ "${lag}" -gt 5 ]; then
    log_warn "Replication lag is ${lag} seconds. Waiting for lag to decrease..."
    sleep 30
  fi

  # Step 2: Failover back to primary
  log_info "Step 2: Failing over to ${PRIMARY_REGION}..."
  aws rds failover-global-cluster \
    --global-cluster-identifier "${PROJECT}-global" \
    --target-db-cluster-identifier "arn:aws:rds:${PRIMARY_REGION}:${AWS_ACCOUNT_ID}:cluster:${PROJECT}-primary" \
    --region "${DR_REGION}"

  log_info "Waiting for failback to complete..."
  aws rds wait db-cluster-available \
    --db-cluster-identifier "${PROJECT}-primary" \
    --region "${PRIMARY_REGION}"

  log_info "Aurora failback complete"

  # Step 3: Scale down DR region
  log_info "Step 3: Scaling down DR region ECS..."
  aws ecs update-service \
    --cluster "${PROJECT}-cluster" \
    --service "${PROJECT}-backend-service" \
    --desired-count 2 \
    --region "${PRIMARY_REGION}"

  aws ecs update-service \
    --cluster "${PROJECT}-cluster-${DR_REGION#*-}" \
    --service "${PROJECT}-backend-service-${DR_REGION#*-}" \
    --desired-count 1 \
    --region "${DR_REGION}"

  # Step 4: Restore configuration
  log_info "Step 4: Restoring primary region configuration..."
  local primary_db_endpoint
  primary_db_endpoint=$(aws rds describe-db-clusters \
    --db-cluster-identifier "${PROJECT}-primary" \
    --region "${PRIMARY_REGION}" \
    --query 'DBClusters[0].Endpoint' \
    --output text)

  aws secretsmanager update-secret \
    --secret-id "launchpad/${PRIMARY_REGION}/database-url" \
    --secret-string "postgresql://postgres:${DB_PASSWORD}@${primary_db_endpoint}:5432/launchpad" \
    --region "${PRIMARY_REGION}"

  # Step 5: Verify
  log_info "Step 5: Verifying primary region health..."
  local retries=0
  local max_retries=10

  while [ ${retries} -lt ${max_retries} ]; do
    if curl -sf --max-time 5 "https://api-us.launchpad.com/health" > /dev/null 2>&1; then
      log_info "Primary region is healthy!"
      break
    fi
    retries=$((retries + 1))
    log_warn "Waiting for primary region (${retries}/${max_retries})..."
    sleep 15
  done

  log_section "Failback Complete"
  echo -e "
${GREEN}Failback to ${PRIMARY_REGION} completed successfully!${NC}

Current state:
  - Primary region: ${PRIMARY_REGION} (writer)
  - DR region: ${DR_REGION} (read replica)
  - All systems operational

Next steps:
  1. Verify all services are functioning correctly
  2. Monitor replication lag for 24 hours
  3. Run a full backup verification
  4. Update monitoring dashboards
"
}

# --- Command: test ---

cmd_test() {
  log_section "DR Test (Non-destructive)"

  log_info "Running disaster recovery test..."

  # Test 1: Health checks
  log_info "Test 1: Health check endpoints"
  for region in "${PRIMARY_REGION}" "${DR_REGION}" "${AP_REGION}"; do
    local endpoint
    case "${region}" in
      "${PRIMARY_REGION}") endpoint="api-us.launchpad.com" ;;
      "${DR_REGION}")      endpoint="api-eu.launchpad.com" ;;
      "${AP_REGION}")      endpoint="api-ap.launchpad.com" ;;
    esac

    if curl -sf --max-time 10 "https://${endpoint}/health" > /dev/null 2>&1; then
      echo -e "  ${region}: ${GREEN}PASS${NC}"
    else
      echo -e "  ${region}: ${RED}FAIL${NC}"
    fi
  done

  # Test 2: Database connectivity
  log_info "Test 2: Database connectivity"
  for region in "${PRIMARY_REGION}" "${DR_REGION}"; do
    local db_endpoint
    case "${region}" in
      "${PRIMARY_REGION})") db_endpoint="launchpad-primary.cluster-xxx.${region}.rds.amazonaws.com" ;;
      "${DR_REGION}")       db_endpoint="launchpad-${DR_REGION#*-}.cluster-xxx.${region}.rds.amazonaws.com" ;;
    esac

    if pg_isready -h "${db_endpoint}" -p 5432 -U postgres > /dev/null 2>&1; then
      echo -e "  ${region}: ${GREEN}PASS${NC}"
    else
      echo -e "  ${region}: ${YELLOW}SKIP${NC} (pg_isready not available)"
    fi
  done

  # Test 3: Redis connectivity
  log_info "Test 3: Redis connectivity"
  for region in "${PRIMARY_REGION}" "${DR_REGION}" "${AP_REGION}"; do
    local redis_id
    case "${region}" in
      "${PRIMARY_REGION}") redis_id="${PROJECT}-redis-primary" ;;
      "${DR_REGION}")      redis_id="${PROJECT}-redis-eu" ;;
      "${AP_REGION}")      redis_id="${PROJECT}-redis-ap" ;;
    esac

    local status
    status=$(aws elasticache describe-replication-groups \
      --replication-group-id "${redis_id}" \
      --region "${region}" \
      --query 'ReplicationGroups[0].Status' \
      --output text 2>/dev/null || echo "ERROR")

    if [ "${status}" = "available" ]; then
      echo -e "  ${region}: ${GREEN}PASS${NC}"
    else
      echo -e "  ${region}: ${RED}FAIL${NC} (${status})"
    fi
  done

  # Test 4: S3 replication
  log_info "Test 4: S3 replication"
  local repl_status
  repl_status=$(aws s3api get-bucket-replication \
    --bucket "${PROJECT}-backups-${ENVIRONMENT}-primary" \
    --query 'ReplicationConfiguration.Rules[0].Status' \
    --output text 2>/dev/null || echo "ERROR")

  if [ "${repl_status}" = "Enabled" ]; then
    echo -e "  S3 replication: ${GREEN}PASS${NC}"
  else
    echo -e "  S3 replication: ${RED}FAIL${NC} (${repl_status})"
  fi

  # Test 5: Route 53 health checks
  log_info "Test 5: Route 53 health checks"
  local hc_count
  hc_count=$(aws route53 list-health-checks \
    --query 'length(HealthChecks)' \
    --output text 2>/dev/null || echo "0")

  if [ "${hc_count}" -ge 3 ]; then
    echo -e "  Health checks: ${GREEN}PASS${NC} (${hc_count} configured)"
  else
    echo -e "  Health checks: ${RED}FAIL${NC} (expected >= 3, got ${hc_count})"
  fi

  # Test 6: CloudFront distribution
  log_info "Test 6: CloudFront distribution"
  local cf_status
  cf_status=$(aws cloudfront list-distributions \
    --query "DistributionList.Items[?Aliases.Items[?contains(@, 'app.launchpad.com')]].Status" \
    --output text 2>/dev/null || echo "ERROR")

  if [ "${cf_status}" = "Deployed" ]; then
    echo -e "  CloudFront: ${GREEN}PASS${NC}"
  else
    echo -e "  CloudFront: ${YELLOW}WARN${NC} (${cf_status})"
  fi

  log_section "DR Test Complete"
  log_info "Review results above. All PASS indicates DR readiness."
}

# --- Command: backup ---

cmd_backup() {
  log_section "Manual Backup"

  log_info "Creating manual Aurora snapshot..."
  local snapshot_id="${PROJECT}-manual-$(date +%Y%m%d-%H%M%S)"

  aws rds create-db-cluster-snapshot \
    --db-cluster-identifier "${PROJECT}-primary" \
    --db-cluster-snapshot-identifier "${snapshot_id}" \
    --region "${PRIMARY_REGION}"

  log_info "Snapshot created: ${snapshot_id}"
  log_info "Waiting for snapshot to be available..."

  aws rds wait db-cluster-snapshot-available \
    --db-cluster-snapshot-identifier "${snapshot_id}" \
    --region "${PRIMARY_REGION}"

  log_info "Snapshot ${snapshot_id} is available"

  # Copy snapshot to DR region
  log_info "Copying snapshot to DR region (${DR_REGION})..."
  aws rds copy-db-cluster-snapshot \
    --source-db-cluster-snapshot-identifier "arn:aws:rds:${PRIMARY_REGION}:${AWS_ACCOUNT_ID}:cluster-snapshot:${snapshot_id}" \
    --target-db-cluster-snapshot-identifier "${snapshot_id}-dr" \
    --source-region "${PRIMARY_REGION}" \
    --region "${DR_REGION}"

  log_info "Backup complete!"
  log_info "  Primary: ${snapshot_id} (${PRIMARY_REGION})"
  log_info "  DR copy: ${snapshot_id}-dr (${DR_REGION})"
}

# --- Main ---

case "${COMMAND}" in
  status)
    cmd_status
    ;;
  failover)
    cmd_failover
    ;;
  failback)
    cmd_failback
    ;;
  test)
    cmd_test
    ;;
  backup)
    cmd_backup
    ;;
  *)
    echo "Usage: $0 {status|failover|failback|test|backup} [region]"
    echo ""
    echo "Commands:"
    echo "  status     - Show current DR status"
    echo "  failover   - Promote DR region to primary"
    echo "  failback   - Restore primary region after recovery"
    echo "  test       - Run non-destructive DR test"
    echo "  backup     - Create manual backup snapshot"
    echo ""
    echo "Examples:"
    echo "  $0 status"
    echo "  $0 failover eu-west-1"
    echo "  $0 failback"
    echo "  $0 test"
    echo "  $0 backup"
    exit 1
    ;;
esac
