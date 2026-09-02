# RDS PostgreSQL Configuration
#
# NOTE: The primary Aurora cluster is now managed by aurora-global.tf
# This file only contains the ElastiCache Redis configuration.
# The aws_db_subnet_group and aws_rds_cluster resources have been
# moved to aurora-global.tf for multi-region support.

# ElastiCache Subnet Group
resource "aws_elasticache_subnet_group" "launchpad" {
  name       = "launchpad-redis-subnet"
  subnet_ids = var.private_subnet_ids

  tags = {
    Name        = "launchpad-redis-subnet"
    Environment = var.environment
  }
}

# ElastiCache Redis
resource "aws_elasticache_cluster" "launchpad" {
  cluster_id           = "launchpad-redis"
  engine               = "redis"
  node_type            = "cache.t3.medium"
  num_cache_nodes      = 1
  parameter_group_name = "default.redis7"
  port                 = 6379
  subnet_group_name    = aws_elasticache_subnet_group.launchpad.name
  security_group_ids   = [aws_security_group.redis.id]

  tags = {
    Environment = var.environment
  }
}
