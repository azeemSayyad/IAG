# Backup Strategy (Step 25.4)

## Automated Backups

### PostgreSQL Backups
- **Frequency:** Every 6 hours
- **Retention:** 30 days
- **Method:** pg_dump + gzip
- **Destination:** S3://launchpad-backups/postgres/

### Redis Backups
- **Frequency:** Every 1 hour
- **Retention:** 7 days
- **Method:** BGSAVE + RDB copy
- **Destination:** S3://launchpad-backups/redis/

### ClickHouse Backups
- **Frequency:** Daily
- **Retention:** 30 days
- **Method:** Native backup
- **Destination:** S3://launchpad-backups/clickhouse/

### Application Backups
- **Frequency:** On deployment
- **Retention:** Last 10 versions
- **Method:** Docker image tags
- **Destination:** ECR

---

## Rollback Procedures

### Database Rollback
```bash
# Restore PostgreSQL from backup
pg_restore -h $DB_HOST -U postgres -d launchpad backup.dump

# Restore Redis
redis-cli -h $REDIS_HOST FLUSHALL
redis-cli -h $REDIS_HOST --rdb backup.rdb
```

### Application Rollback
```bash
# Rollback to previous version
kubectl rollout undo deployment/backend-api -n launchpad

# Rollback to specific revision
kubectl rollout undo deployment/backend-api -n launchpad --to-revision=2

# Check rollout status
kubectl rollout status deployment/backend-api -n launchpad
```

### Blue-Green Rollback
```bash
# Switch service back to blue
kubectl patch service backend-api -n launchpad -p '{"spec":{"selector":{"version":"blue"}}}'
```

### Canary Rollback
```bash
# Scale down canary
kubectl scale deployment/backend-api-canary -n launchpad --replicas=0

# Remove canary ingress
kubectl delete ingress backend-api-canary -n launchpad
```

---

## Backup Scripts

### PostgreSQL Backup
```bash
#!/bin/bash
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="launchpad_postgres_${TIMESTAMP}.dump"

pg_dump -h $DB_HOST -U postgres -Fc launchpad > $BACKUP_FILE
gzip $BACKUP_FILE

aws s3 cp ${BACKUP_FILE}.gz s3://launchpad-backups/postgres/
rm ${BACKUP_FILE}.gz
```

### Redis Backup
```bash
#!/bin/bash
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="launchpad_redis_${TIMESTAMP}.rdb"

redis-cli -h $REDIS_HOST BGSAVE
sleep 5
redis-cli -h $REDIS_HOST --rdb $BACKUP_FILE
gzip $BACKUP_FILE

aws s3 cp ${BACKUP_FILE}.gz s3://launchpad-backups/redis/
rm ${BACKUP_FILE}.gz
```

---

## Monitoring

### Backup Health Checks
- Verify backup completion
- Check backup size
- Validate backup integrity
- Alert on failures

### Restore Testing
- Monthly restore test
- Verify data integrity
- Document recovery time
- Update procedures

---

## RTO/RPO Targets

| Component | RTO | RPO |
|-----------|-----|-----|
| Database | 1 hour | 6 hours |
| Redis | 15 minutes | 1 hour |
| Application | 30 minutes | N/A |
| File Storage | 1 hour | 1 hour |
