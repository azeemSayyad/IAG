# Aurora Global Database (Phase 50.2)
#
# Global database replication across regions:
# - Primary: us-east-1 (writer + 2 read replicas)
# - Secondary: eu-west-1 (1 read replica, promoted on failover)
# - Secondary: ap-southeast-1 (1 read replica, promoted on failover)
#
# Features:
# - Cross-region replication lag < 1 second
# - Automated failover with RPO < 1 second, RTO < 1 minute
# - Backtrack for point-in-time recovery (up to 72 hours)
# - Global failover via AWS CLI or automation script

# --- Aurora Global Cluster ---

resource "aws_rds_global_cluster" "launchpad" {
  global_cluster_identifier = "launchpad-global"
  engine                    = "aurora-postgresql"
  engine_version            = "15.4"
  database_name             = "launchpad"
  storage_encrypted         = true
  deletion_protection       = true

  tags = {
    Name        = "launchpad-global-cluster"
    Environment = var.environment
  }
}

# --- Primary Cluster (us-east-1) ---

resource "aws_rds_cluster" "primary" {
  provider = aws.primary

  cluster_identifier              = "launchpad-primary"
  engine                          = "aurora-postgresql"
  engine_version                  = "15.4"
  global_cluster_identifier       = aws_rds_global_cluster.launchpad.id
  database_name                   = "launchpad"
  master_username                 = "postgres"
  master_password                 = var.db_password
  db_subnet_group_name            = aws_db_subnet_group.primary.name
  vpc_security_group_ids          = [aws_security_group.rds.id]
  storage_encrypted               = true
  backup_retention_period         = 7
  preferred_backup_window         = "03:00-04:00"
  preferred_maintenance_window    = "sun:04:00-sun:05:00"
  skip_final_snapshot             = false
  final_snapshot_identifier       = "launchpad-primary-final"
  enable_global_write_forwarding  = false
  apply_immediately               = false

  backtrack_window = 72  # 72 hours of backtrack

  tags = {
    Name        = "launchpad-primary-cluster"
    Region      = "us-east-1"
    Role        = "primary"
    Environment = var.environment
  }
}

resource "aws_rds_cluster_instance" "primary_writer" {
  provider = aws.primary

  identifier           = "launchpad-primary-writer"
  cluster_identifier   = aws_rds_cluster.primary.id
  instance_class       = "db.r6g.large"
  engine               = aws_rds_cluster.primary.engine
  engine_version       = aws_rds_cluster.primary.engine_version
  publicly_accessible  = false

  tags = {
    Name        = "launchpad-primary-writer"
    Region      = "us-east-1"
    Role        = "writer"
    Environment = var.environment
  }
}

resource "aws_rds_cluster_instance" "primary_reader" {
  provider = aws.primary

  identifier           = "launchpad-primary-reader"
  cluster_identifier   = aws_rds_cluster.primary.id
  instance_class       = "db.r6g.large"
  engine               = aws_rds_cluster.primary.engine
  engine_version       = aws_rds_cluster.primary.engine_version
  publicly_accessible  = false

  tags = {
    Name        = "launchpad-primary-reader"
    Region      = "us-east-1"
    Role        = "reader"
    Environment = var.environment
  }
}

# --- Secondary Cluster (eu-west-1) ---

resource "aws_rds_cluster" "eu_west" {
  provider = aws.eu_west

  cluster_identifier              = "launchpad-eu"
  engine                          = "aurora-postgresql"
  engine_version                  = "15.4"
  global_cluster_identifier       = aws_rds_global_cluster.launchpad.id
  db_subnet_group_name            = aws_db_subnet_group.eu_west.name
  vpc_security_group_ids          = [aws_security_group.rds_eu.id]
  storage_encrypted               = true
  backup_retention_period         = 7
  preferred_backup_window         = "03:00-04:00"
  preferred_maintenance_window    = "sun:05:00-sun:06:00"
  skip_final_snapshot             = false
  final_snapshot_identifier       = "launchpad-eu-final"
  apply_immediately               = false

  # Secondary cluster inherits database from global cluster
  depends_on = [aws_rds_cluster_instance.primary_writer]

  tags = {
    Name        = "launchpad-eu-cluster"
    Region      = "eu-west-1"
    Role        = "secondary"
    Environment = var.environment
  }
}

resource "aws_rds_cluster_instance" "eu_west_reader" {
  provider = aws.eu_west

  identifier           = "launchpad-eu-reader"
  cluster_identifier   = aws_rds_cluster.eu_west.id
  instance_class       = "db.r6g.medium"
  engine               = aws_rds_cluster.eu_west.engine
  engine_version       = aws_rds_cluster.eu_west.engine_version
  publicly_accessible  = false

  tags = {
    Name        = "launchpad-eu-reader"
    Region      = "eu-west-1"
    Role        = "reader"
    Environment = var.environment
  }
}

# --- Secondary Cluster (ap-southeast-1) ---

resource "aws_rds_cluster" "ap_southeast" {
  provider = aws.ap_southeast

  cluster_identifier              = "launchpad-ap"
  engine                          = "aurora-postgresql"
  engine_version                  = "15.4"
  global_cluster_identifier       = aws_rds_global_cluster.launchpad.id
  db_subnet_group_name            = aws_db_subnet_group.ap_southeast.name
  vpc_security_group_ids          = [aws_security_group.rds_ap.id]
  storage_encrypted               = true
  backup_retention_period         = 7
  preferred_backup_window         = "03:00-04:00"
  preferred_maintenance_window    = "sun:06:00-sun:07:00"
  skip_final_snapshot             = false
  final_snapshot_identifier       = "launchpad-ap-final"
  apply_immediately               = false

  depends_on = [aws_rds_cluster_instance.primary_writer]

  tags = {
    Name        = "launchpad-ap-cluster"
    Region      = "ap-southeast-1"
    Role        = "secondary"
    Environment = var.environment
  }
}

resource "aws_rds_cluster_instance" "ap_southeast_reader" {
  provider = aws.ap_southeast

  identifier           = "launchpad-ap-reader"
  cluster_identifier   = aws_rds_cluster.ap_southeast.id
  instance_class       = "db.r6g.medium"
  engine               = aws_rds_cluster.ap_southeast.engine
  engine_version       = aws_rds_cluster.ap_southeast.engine_version
  publicly_accessible  = false

  tags = {
    Name        = "launchpad-ap-reader"
    Region      = "ap-southeast-1"
    Role        = "reader"
    Environment = var.environment
  }
}

# --- DB Subnet Groups (per region) ---

resource "aws_db_subnet_group" "primary" {
  provider = aws.primary

  name       = "launchpad-db-subnet-primary"
  subnet_ids = var.private_subnet_ids

  tags = {
    Name        = "launchpad-db-subnet-primary"
    Region      = "us-east-1"
    Environment = var.environment
  }
}

resource "aws_db_subnet_group" "eu_west" {
  provider = aws.eu_west

  name       = "launchpad-db-subnet-eu"
  subnet_ids = var.eu_private_subnet_ids

  tags = {
    Name        = "launchpad-db-subnet-eu"
    Region      = "eu-west-1"
    Environment = var.environment
  }
}

resource "aws_db_subnet_group" "ap_southeast" {
  provider = aws.ap_southeast

  name       = "launchpad-db-subnet-ap"
  subnet_ids = var.ap_private_subnet_ids

  tags = {
    Name        = "launchpad-db-subnet-ap"
    Region      = "ap-southeast-1"
    Environment = var.environment
  }
}

# --- Security Groups (per region) ---

resource "aws_security_group" "rds_eu" {
  provider = aws.eu_west

  name_prefix = "launchpad-rds-eu-"
  vpc_id      = var.eu_vpc_id

  ingress {
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.ecs_eu.id]
  }

  tags = {
    Name        = "launchpad-rds-eu-sg"
    Region      = "eu-west-1"
    Environment = var.environment
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_security_group" "rds_ap" {
  provider = aws.ap_southeast

  name_prefix = "launchpad-rds-ap-"
  vpc_id      = var.ap_vpc_id

  ingress {
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.ecs_ap.id]
  }

  tags = {
    Name        = "launchpad-rds-ap-sg"
    Region      = "ap-southeast-1"
    Environment = var.environment
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_security_group" "ecs_eu" {
  provider = aws.eu_west

  name_prefix = "launchpad-ecs-eu-"
  vpc_id      = var.eu_vpc_id

  ingress {
    from_port       = 8000
    to_port         = 8000
    protocol        = "tcp"
    security_groups = [aws_security_group.alb_eu.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name        = "launchpad-ecs-eu-sg"
    Region      = "eu-west-1"
    Environment = var.environment
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_security_group" "ecs_ap" {
  provider = aws.ap_southeast

  name_prefix = "launchpad-ecs-ap-"
  vpc_id      = var.ap_vpc_id

  ingress {
    from_port       = 8000
    to_port         = 8000
    protocol        = "tcp"
    security_groups = [aws_security_group.alb_ap.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name        = "launchpad-ecs-ap-sg"
    Region      = "ap-southeast-1"
    Environment = var.environment
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_security_group" "alb_eu" {
  provider = aws.eu_west

  name_prefix = "launchpad-alb-eu-"
  vpc_id      = var.eu_vpc_id

  ingress {
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name        = "launchpad-alb-eu-sg"
    Region      = "eu-west-1"
    Environment = var.environment
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_security_group" "alb_ap" {
  provider = aws.ap_southeast

  name_prefix = "launchpad-alb-ap-"
  vpc_id      = var.ap_vpc_id

  ingress {
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name        = "launchpad-alb-ap-sg"
    Region      = "ap-southeast-1"
    Environment = var.environment
  }

  lifecycle {
    create_before_destroy = true
  }
}

# --- CloudWatch Alarms for Replication Lag ---

resource "aws_cloudwatch_metric_alarm" "replication_lag_eu" {
  provider = aws.eu_west

  alarm_name          = "launchpad-replication-lag-eu"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  metric_name         = "AuroraGlobalDBReplicationLag"
  namespace           = "AWS/RDS"
  period              = 60
  statistic           = "Average"
  threshold           = 5  # 5 seconds
  alarm_description   = "Aurora Global DB replication lag to EU exceeds 5 seconds"
  treat_missing_data  = "notBreaching"

  dimensions = {
    DBClusterIdentifier = aws_rds_cluster.eu_west.cluster_identifier
  }

  alarm_actions = [aws_sns_topic.alerts.arn]
  ok_actions    = [aws_sns_topic.alerts.arn]

  tags = {
    Name        = "launchpad-replication-lag-eu"
    Region      = "eu-west-1"
    Environment = var.environment
  }
}

resource "aws_cloudwatch_metric_alarm" "replication_lag_ap" {
  provider = aws.ap_southeast

  alarm_name          = "launchpad-replication-lag-ap"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  metric_name         = "AuroraGlobalDBReplicationLag"
  namespace           = "AWS/RDS"
  period              = 60
  statistic           = "Average"
  threshold           = 5
  alarm_description   = "Aurora Global DB replication lag to AP exceeds 5 seconds"
  treat_missing_data  = "notBreaching"

  dimensions = {
    DBClusterIdentifier = aws_rds_cluster.ap_southeast.cluster_identifier
  }

  alarm_actions = [aws_sns_topic.alerts.arn]
  ok_actions    = [aws_sns_topic.alerts.arn]

  tags = {
    Name        = "launchpad-replication-lag-ap"
    Region      = "ap-southeast-1"
    Environment = var.environment
  }
}

# --- SNS Topic for Alerts ---

resource "aws_sns_topic" "alerts" {
  name = "launchpad-global-alerts"

  tags = {
    Name        = "launchpad-global-alerts"
    Environment = var.environment
  }
}

# --- Outputs ---

output "global_cluster_id" {
  description = "Aurora Global Cluster ID"
  value       = aws_rds_global_cluster.launchpad.id
}

output "primary_cluster_endpoint" {
  description = "Primary cluster writer endpoint"
  value       = aws_rds_cluster.primary.endpoint
}

output "primary_cluster_reader_endpoint" {
  description = "Primary cluster reader endpoint"
  value       = aws_rds_cluster.primary.reader_endpoint
}

output "eu_cluster_endpoint" {
  description = "EU cluster reader endpoint"
  value       = aws_rds_cluster.eu_west.endpoint
}

output "ap_cluster_endpoint" {
  description = "AP cluster reader endpoint"
  value       = aws_rds_cluster.ap_southeast.endpoint
}
