# Disaster Recovery Plan

## Recovery Time Objectives (RTO) and Recovery Point Objectives (RPO)

| Component | RTO | RPO | Strategy |
|-----------|-----|-----|----------|
| Database | 1 hour | 5 minutes | Automated backups + point-in-time recovery |
| Redis | 15 minutes | 1 minute | Redis Sentinel + AOF persistence |
| Application | 30 minutes | N/A | Multi-AZ deployment + auto-scaling |
| File Storage | 1 hour | 1 hour | S3 cross-region replication |

## Backup Strategy

### PostgreSQL Backups

- **Frequency**: Every 6 hours
- **Retention**: 30 days
- **Method**: pg_dump + S3 upload
- **Point-in-time**: WAL archiving every 5 minutes

```bash
# Backup command
pg_dump -h $DB_HOST -U $DB_USER -d launchpad | gzip > backup_$(date +%Y%m%d_%H%M%S).sql.gz

# Upload to S3
aws s3 cp backup_*.sql.gz s3://launchpad-backups/postgres/
```

### Redis Backups

- **Frequency**: Every 1 hour
- **Retention**: 7 days
- **Method**: RDB snapshots + AOF

```bash
# Trigger backup
redis-cli BGSAVE

# Copy RDB file
cp /var/lib/redis/dump.rdb /backups/redis/dump_$(date +%Y%m%d_%H%M%S).rdb
```

### Application State

- **Configuration**: Stored in Git + Kubernetes ConfigMaps
- **Secrets**: Kubernetes Secrets + AWS Secrets Manager
- **User uploads**: S3 with versioning enabled

## Recovery Procedures

### Database Recovery

1. **Identify the issue**
   - Check monitoring dashboards
   - Review error logs

2. **Assess damage**
   - Determine if point-in-time recovery is needed
   - Identify affected data

3. **Restore from backup**
   ```bash
   # Download latest backup
   aws s3 cp s3://launchpad-backups/postgres/latest.sql.gz .

   # Restore
   gunzip < latest.sql.gz | psql -h $DB_HOST -U $DB_USER -d launchpad
   ```

4. **Verify restoration**
   - Run data integrity checks
   - Verify application connectivity
   - Check recent transactions

### Redis Recovery

1. **Stop Redis**
   ```bash
   systemctl stop redis
   ```

2. **Restore RDB**
   ```bash
   cp /backups/redis/dump_latest.rdb /var/lib/redis/dump.rdb
   ```

3. **Start Redis**
   ```bash
   systemctl start redis
   ```

4. **Verify**
   ```bash
   redis-cli ping
   redis-cli dbsize
   ```

### Application Recovery

1. **Kubernetes rollback**
   ```bash
   kubectl rollout undo deployment/backend-api -n launchpad
   ```

2. **Scale up**
   ```bash
   kubectl scale deployment/backend-api --replicas=5 -n launchpad
   ```

3. **Verify health**
   ```bash
   kubectl get pods -n launchpad
   curl https://api.launchpad.com/health
   ```

## Failover Procedures

### Database Failover

- **Automatic**: Aurora automatic failover (30-60 seconds)
- **Manual**: Promote read replica to primary

### Application Failover

- **Kubernetes**: Pod rescheduling on node failure
- **Multi-AZ**: Traffic routing to healthy AZ

### DNS Failover

- **Route 53**: Health checks + failover routing
- **TTL**: 60 seconds for fast propagation

## Communication Plan

### Incident Severity Levels

| Level | Description | Response Time | Communication |
|-------|-------------|---------------|---------------|
| P1 | Complete outage | 15 minutes | All stakeholders |
| P2 | Major feature broken | 30 minutes | Team leads |
| P3 | Minor issue | 2 hours | Development team |
| P4 | Cosmetic issue | 24 hours | Backlog |

### Communication Channels

- **Slack**: #incident-response
- **Email**: incidents@launchpad.com
- **Status Page**: status.launchpad.com

## Testing Schedule

| Test | Frequency | Last Run | Next Run |
|------|-----------|----------|----------|
| Database backup restore | Monthly | - | - |
| Redis failover | Quarterly | - | - |
| Full DR drill | Annually | - | - |
| Backup verification | Weekly | - | - |

## Contact Information

| Role | Name | Email | Phone |
|------|------|-------|-------|
| DBA | - | dba@launchpad.com | - |
| DevOps Lead | - | devops@launchpad.com | - |
| On-call Engineer | - | oncall@launchpad.com | - |
