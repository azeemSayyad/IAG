# Disaster Recovery Configuration (Phase 50.5)
#
# Cross-region disaster recovery with:
# - S3 cross-region replication for backups
# - Aurora Global Database with backtrack + failover
# - ElastiCache Redis backup to S3
# - Automated backup verification
# - RTO < 15 minutes, RPO < 1 minute
#
# Failover Procedure:
# 1. Promote Aurora secondary cluster to standalone
# 2. Update Route 53 health checks (automatic)
# 3. Scale up ECS in secondary region
# 4. Restore Redis from S3 backup
# 5. Verify application health

# --- S3 Backup Bucket (Primary) ---
# NOTE: The primary backup bucket is defined in multi-region.tf
# This file only defines the DR replica bucket and replication config.

resource "aws_s3_bucket_server_side_encryption_configuration" "backups_primary" {
  bucket = aws_s3_bucket.backups_primary.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "backups_primary" {
  provider = aws.primary

  bucket = aws_s3_bucket.backups_primary.id

  rule {
    id     = "transition-to-glacier"
    status = "Enabled"

    transition {
      days          = 30
      storage_class = "GLACIER"
    }

    expiration {
      days = 365
    }
  }
}

# --- S3 Backup Bucket (DR Region) ---

resource "aws_s3_bucket" "backups_dr" {
  provider = aws.eu_west

  bucket = "launchpad-backups-${var.environment}-dr"

  tags = {
    Name        = "launchpad-backups-dr"
    Region      = "eu-west-1"
    Environment = var.environment
  }
}

resource "aws_s3_bucket_versioning" "backups_dr" {
  provider = aws.eu_west

  bucket = aws_s3_bucket.backups_dr.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "backups_dr" {
  provider = aws.eu_west

  bucket = aws_s3_bucket.backups_dr.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# --- S3 Cross-Region Replication for Backups ---

resource "aws_s3_bucket_replication_configuration" "backups" {
  provider = aws.primary

  role   = aws_iam_role.s3_replication.arn
  bucket = aws_s3_bucket.backups_primary.id

  rule {
    id     = "replicate-backups-to-dr"
    status = "Enabled"

    destination {
      bucket        = aws_s3_bucket.backups_dr.arn
      storage_class = "STANDARD"

      replication_time {
        status = "Enabled"
        time {
          minutes = 15
        }
      }

      metrics {
        status = "Enabled"
        event_threshold {
          minutes = 15
        }
      }
    }

    source_selection_criteria {
      replica_modifications {
        status = "Enabled"
      }
    }
  }
}

# --- Aurora Backup Configuration ---

resource "aws_rds_cluster_snapshot" "daily" {
  provider = aws.primary

  db_cluster_identifier          = aws_rds_cluster.primary.id
  db_cluster_snapshot_identifier = "launchpad-daily-${formatdate("YYYY-MM-DD", timestamp())}"

  tags = {
    Name        = "launchpad-daily-snapshot"
    Region      = "us-east-1"
    Environment = var.environment
    BackupType  = "daily"
  }

  lifecycle {
    ignore_changes = [db_cluster_snapshot_identifier]
  }
}

# --- CloudWatch Alarms for DR ---

resource "aws_cloudwatch_metric_alarm" "primary_db_cpu" {
  provider = aws.primary

  alarm_name          = "launchpad-primary-db-cpu"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 5
  metric_name         = "CPUUtilization"
  namespace           = "AWS/RDS"
  period              = 60
  statistic           = "Average"
  threshold           = 80
  alarm_description   = "Primary DB CPU > 80% for 5 minutes"
  treat_missing_data  = "notBreaching"

  dimensions = {
    DBClusterIdentifier = aws_rds_cluster.primary.cluster_identifier
  }

  alarm_actions = [aws_sns_topic.dr_alerts.arn]
  ok_actions    = [aws_sns_topic.dr_alerts.arn]

  tags = {
    Name        = "launchpad-primary-db-cpu"
    Environment = var.environment
  }
}

resource "aws_cloudwatch_metric_alarm" "primary_db_connections" {
  provider = aws.primary

  alarm_name          = "launchpad-primary-db-connections"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  metric_name         = "DatabaseConnections"
  namespace           = "AWS/RDS"
  period              = 60
  statistic           = "Average"
  threshold           = 150
  alarm_description   = "Primary DB connections > 150"
  treat_missing_data  = "notBreaching"

  dimensions = {
    DBClusterIdentifier = aws_rds_cluster.primary.cluster_identifier
  }

  alarm_actions = [aws_sns_topic.dr_alerts.arn]

  tags = {
    Name        = "launchpad-primary-db-connections"
    Environment = var.environment
  }
}

resource "aws_cloudwatch_metric_alarm" "alb_5xx_primary" {
  provider = aws.primary

  alarm_name          = "launchpad-alb-5xx-primary"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  metric_name         = "HTTPCode_Target_5XX_Count"
  namespace           = "AWS/ApplicationELB"
  period              = 60
  statistic           = "Sum"
  threshold           = 50
  alarm_description   = "Primary ALB 5xx errors > 50 in 1 minute"
  treat_missing_data  = "notBreaching"

  dimensions = {
    LoadBalancer = aws_lb.regional["us-east-1"].arn_suffix
  }

  alarm_actions = [aws_sns_topic.dr_alerts.arn]

  tags = {
    Name        = "launchpad-alb-5xx-primary"
    Environment = var.environment
  }
}

# --- SNS Topic for DR Alerts ---

resource "aws_sns_topic" "dr_alerts" {
  name = "launchpad-dr-alerts"

  tags = {
    Name        = "launchpad-dr-alerts"
    Environment = var.environment
  }
}

# --- SNS Topic Subscription (Email) ---

resource "aws_sns_topic_subscription" "dr_email" {
  topic_arn = aws_sns_topic.dr_alerts.arn
  protocol  = "email"
  endpoint  = "ops@launchpad.com"
}

# --- ElastiCache Redis Backup Configuration ---

resource "aws_elasticache_replication_group" "primary" {
  provider = aws.primary

  replication_group_id = "launchpad-redis-primary"
  description          = "Launchpad Redis primary cluster"
  node_type            = "cache.r6g.large"
  num_cache_clusters   = 2
  port                 = 6379

  automatic_failover_enabled = true
  multi_az_enabled           = true
  at_rest_encryption_enabled = true
  transit_encryption_enabled = true

  snapshot_retention_limit = 7
  snapshot_window          = "03:00-05:00"
  maintenance_window       = "sun:05:00-sun:06:00"

  subnet_group_name  = aws_elasticache_subnet_group.primary.name
  security_group_ids = [aws_security_group.redis.id]

  tags = {
    Name        = "launchpad-redis-primary"
    Region      = "us-east-1"
    Environment = var.environment
  }
}

resource "aws_elasticache_replication_group" "eu" {
  provider = aws.eu_west

  replication_group_id = "launchpad-redis-eu"
  description          = "Launchpad Redis EU cluster"
  node_type            = "cache.r6g.medium"
  num_cache_clusters   = 2
  port                 = 6379

  automatic_failover_enabled = true
  multi_az_enabled           = true
  at_rest_encryption_enabled = true
  transit_encryption_enabled = true

  snapshot_retention_limit = 7
  snapshot_window          = "03:00-05:00"
  maintenance_window       = "sun:06:00-sun:07:00"

  subnet_group_name  = aws_elasticache_subnet_group.eu.name
  security_group_ids = [aws_security_group.redis_eu.id]

  tags = {
    Name        = "launchpad-redis-eu"
    Region      = "eu-west-1"
    Environment = var.environment
  }
}

resource "aws_elasticache_replication_group" "ap" {
  provider = aws.ap_southeast

  replication_group_id = "launchpad-redis-ap"
  description          = "Launchpad Redis AP cluster"
  node_type            = "cache.r6g.medium"
  num_cache_clusters   = 2
  port                 = 6379

  automatic_failover_enabled = true
  multi_az_enabled           = true
  at_rest_encryption_enabled = true
  transit_encryption_enabled = true

  snapshot_retention_limit = 7
  snapshot_window          = "03:00-05:00"
  maintenance_window       = "sun:07:00-sun:08:00"

  subnet_group_name  = aws_elasticache_subnet_group.ap.name
  security_group_ids = [aws_security_group.redis_ap.id]

  tags = {
    Name        = "launchpad-redis-ap"
    Region      = "ap-southeast-1"
    Environment = var.environment
  }
}

# --- ElastiCache Subnet Groups ---

resource "aws_elasticache_subnet_group" "primary" {
  provider = aws.primary

  name       = "launchpad-redis-subnet-primary"
  subnet_ids = var.private_subnet_ids

  tags = {
    Name        = "launchpad-redis-subnet-primary"
    Environment = var.environment
  }
}

resource "aws_elasticache_subnet_group" "eu" {
  provider = aws.eu_west

  name       = "launchpad-redis-subnet-eu"
  subnet_ids = var.eu_private_subnet_ids

  tags = {
    Name        = "launchpad-redis-subnet-eu"
    Environment = var.environment
  }
}

resource "aws_elasticache_subnet_group" "ap" {
  provider = aws.ap_southeast

  name       = "launchpad-redis-subnet-ap"
  subnet_ids = var.ap_private_subnet_ids

  tags = {
    Name        = "launchpad-redis-subnet-ap"
    Environment = var.environment
  }
}

# --- ElastiCache Security Groups ---

resource "aws_security_group" "redis_eu" {
  provider = aws.eu_west

  name_prefix = "launchpad-redis-eu-"
  vpc_id      = var.eu_vpc_id

  ingress {
    from_port       = 6379
    to_port         = 6379
    protocol        = "tcp"
    security_groups = [aws_security_group.ecs_eu.id]
  }

  tags = {
    Name        = "launchpad-redis-eu-sg"
    Region      = "eu-west-1"
    Environment = var.environment
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_security_group" "redis_ap" {
  provider = aws.ap_southeast

  name_prefix = "launchpad-redis-ap-"
  vpc_id      = var.ap_vpc_id

  ingress {
    from_port       = 6379
    to_port         = 6379
    protocol        = "tcp"
    security_groups = [aws_security_group.ecs_ap.id]
  }

  tags = {
    Name        = "launchpad-redis-ap-sg"
    Region      = "ap-southeast-1"
    Environment = var.environment
  }

  lifecycle {
    create_before_destroy = true
  }
}

# --- DR Status Output ---

output "dr_status" {
  description = "Disaster recovery configuration status"
  value = {
    primary_region          = "us-east-1"
    dr_region               = "eu-west-1"
    aurora_global_cluster   = aws_rds_global_cluster.launchpad.id
    backup_bucket_primary   = aws_s3_bucket.backups_primary.bucket
    backup_bucket_dr        = aws_s3_bucket.backups_dr.bucket
    redis_primary           = aws_elasticache_replication_group.primary.id
    redis_eu                = aws_elasticache_replication_group.eu.id
    redis_ap                = aws_elasticache_replication_group.ap.id
    sns_alerts_topic        = aws_sns_topic.dr_alerts.arn
  }
}
