#!/bin/bash

# Backup Script for Launchpad Call Center
# Run via cron: 0 */6 * * * /path/to/backup.sh

set -e

# Configuration
BACKUP_DIR="/backups"
S3_BUCKET="s3://launchpad-backups"
RETENTION_DAYS=30
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

# Database configuration
DB_HOST=${DB_HOST:-"localhost"}
DB_PORT=${DB_PORT:-"5432"}
DB_NAME=${DB_NAME:-"launchpad"}
DB_USER=${DB_USER:-"postgres"}

# Create backup directory
mkdir -p $BACKUP_DIR/postgres
mkdir -p $BACKUP_DIR/redis
mkdir -p $BACKUP_DIR/logs

echo "=== Starting Backup: $TIMESTAMP ==="

# PostgreSQL Backup
echo "Backing up PostgreSQL..."
pg_dump -h $DB_HOST -p $DB_PORT -U $DB_USER -d $DB_NAME | gzip > $BACKUP_DIR/postgres/backup_$TIMESTAMP.sql.gz

# Upload to S3
if command -v aws &> /dev/null; then
    echo "Uploading to S3..."
    aws s3 cp $BACKUP_DIR/postgres/backup_$TIMESTAMP.sql.gz $S3_BUCKET/postgres/
fi

# Redis Backup
echo "Backing up Redis..."
redis-cli BGSAVE
sleep 5
if [ -f /var/lib/redis/dump.rdb ]; then
    cp /var/lib/redis/dump.rdb $BACKUP_DIR/redis/dump_$TIMESTAMP.rdb
    if command -v aws &> /dev/null; then
        aws s3 cp $BACKUP_DIR/redis/dump_$TIMESTAMP.rdb $S3_BUCKET/redis/
    fi
fi

# Cleanup old backups
echo "Cleaning up old backups..."
find $BACKUP_DIR -name "backup_*.sql.gz" -mtime +$RETENTION_DAYS -delete
find $BACKUP_DIR -name "dump_*.rdb" -mtime +7 -delete

# Log backup completion
echo "=== Backup Completed: $(date) ===" >> $BACKUP_DIR/logs/backup.log

# Verify backup integrity
echo "Verifying backup integrity..."
if [ -f $BACKUP_DIR/postgres/backup_$TIMESTAMP.sql.gz ]; then
    gzip -t $BACKUP_DIR/postgres/backup_$TIMESTAMP.sql.gz && echo "PostgreSQL backup verified" || echo "PostgreSQL backup verification FAILED"
fi

echo "=== Backup Process Complete ==="
