# Multi-Region Deployment Configuration (Phase 50.1)
#
# Deploys Launchpad Call Center across 3 AWS regions:
# - us-east-1: Primary (all services)
# - eu-west-1: Europe (API + Workers)
# - ap-southeast-1: Asia (API + Workers)
#
# Usage:
#   terraform apply -var="region=us-east-1"  # Primary
#   terraform apply -var="region=eu-west-1"  # Europe
#   terraform apply -var="region=ap-southeast-1"  # Asia

# --- Region Configuration ---

variable "regions" {
  description = "Multi-region deployment configuration"
  type = map(object({
    enabled             = bool
    is_primary          = bool
    ecr_repository      = string
    ecs_cluster         = string
    ecs_service         = string
    desired_count       = number
    cpu                 = string
    memory              = string
    db_instance_class   = string
    redis_node_type     = string
    domain_prefix       = string
  }))
  default = {
    "us-east-1" = {
      enabled             = true
      is_primary          = true
      ecr_repository      = "launchpad"
      ecs_cluster         = "launchpad-cluster"
      ecs_service         = "launchpad-backend-service"
      desired_count       = 2
      cpu                 = "512"
      memory              = "1024"
      db_instance_class   = "db.r6g.large"
      redis_node_type     = "cache.t3.medium"
      domain_prefix       = "api"
    }
    "eu-west-1" = {
      enabled             = true
      is_primary          = false
      ecr_repository      = "launchpad"
      ecs_cluster         = "launchpad-cluster-eu"
      ecs_service         = "launchpad-backend-service-eu"
      desired_count       = 2
      cpu                 = "512"
      memory              = "1024"
      db_instance_class   = "db.r6g.medium"
      redis_node_type     = "cache.t3.small"
      domain_prefix       = "api-eu"
    }
    "ap-southeast-1" = {
      enabled             = true
      is_primary          = false
      ecr_repository      = "launchpad"
      ecs_cluster         = "launchpad-cluster-ap"
      ecs_service         = "launchpad-backend-service-ap"
      desired_count       = 1
      cpu                 = "512"
      memory              = "1024"
      db_instance_class   = "db.r6g.medium"
      redis_node_type     = "cache.t3.small"
      domain_prefix       = "api-ap"
    }
  }
}

# --- ECR Repositories (per region) ---

resource "aws_ecr_repository" "backend" {
  for_each = { for k, v in var.regions : k => v if v.enabled }

  name                 = each.value.ecr_repository
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "AES256"
  }

  tags = {
    Name        = "launchpad-backend-${each.key}"
    Region      = each.key
    Environment = var.environment
  }
}

# --- ECS Clusters (per region) ---

resource "aws_ecs_cluster" "regional" {
  for_each = { for k, v in var.regions : k => v if v.enabled }

  name = each.value.ecs_cluster

  setting {
    name  = "containerInsights"
    value = "enabled"
  }

  configuration {
    execute_command_configuration {
      logging = "OVERRIDE"
      log_configuration {
        cloud_watch_log_group_name = "/ecs/${each.value.ecs_cluster}"
      }
    }
  }

  tags = {
    Name        = each.value.ecs_cluster
    Region      = each.key
    Environment = var.environment
  }
}

# --- CloudWatch Log Groups (per region) ---

resource "aws_cloudwatch_log_group" "ecs" {
  for_each = { for k, v in var.regions : k => v if v.enabled }

  name              = "/ecs/${each.value.ecs_cluster}"
  retention_in_days = 30

  tags = {
    Name        = "${each.value.ecs_cluster}-logs"
    Region      = each.key
    Environment = var.environment
  }
}

# --- ECS Task Definitions (per region) ---

resource "aws_ecs_task_definition" "backend" {
  for_each = { for k, v in var.regions : k => v if v.enabled }

  family                   = "launchpad-backend-${each.key}"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = each.value.cpu
  memory                   = each.value.memory
  execution_role_arn       = aws_iam_role.ecs_execution.arn
  task_role_arn            = aws_iam_role.ecs_task.arn

  container_definitions = jsonencode([
    {
      name      = "backend-api"
      image     = "${aws_ecr_repository.backend[each.key].repository_url}:latest"
      essential = true

      portMappings = [
        {
          containerPort = 8000
          hostPort      = 8000
          protocol      = "tcp"
        }
      ]

      environment = [
        { name = "APP_ENV", value = var.environment },
        { name = "APP_PORT", value = "8000" },
        { name = "AWS_REGION", value = each.key },
        { name = "DEPLOYMENT_REGION", value = each.key },
        { name = "IS_PRIMARY_REGION", value = tostring(each.value.is_primary) },
      ]

      secrets = [
        { name = "DATABASE_URL", valueFrom = "arn:aws:secretsmanager:${each.key}:${data.aws_caller_identity.current.account_id}:secret:launchpad/${each.key}/database-url" },
        { name = "POSTGRES_URL", valueFrom = "arn:aws:secretsmanager:${each.key}:${data.aws_caller_identity.current.account_id}:secret:launchpad/${each.key}/database-url" },
        { name = "JWT_SECRET", valueFrom = "arn:aws:secretsmanager:${each.key}:${data.aws_caller_identity.current.account_id}:secret:launchpad/${each.key}/jwt-secret" },
        { name = "REDIS_URL", valueFrom = "arn:aws:secretsmanager:${each.key}:${data.aws_caller_identity.current.account_id}:secret:launchpad/${each.key}/redis-url" },
        { name = "ENGAGECLOUD_API_KEY", valueFrom = "arn:aws:secretsmanager:${each.key}:${data.aws_caller_identity.current.account_id}:secret:launchpad/${each.key}/engagecloud-api-key" },
        { name = "ENGAGECLOUD_API_SECRET", valueFrom = "arn:aws:secretsmanager:${each.key}:${data.aws_caller_identity.current.account_id}:secret:launchpad/${each.key}/engagecloud-api-secret" },
        { name = "ENGAGECLOUD_AGENCY_ID", valueFrom = "arn:aws:secretsmanager:${each.key}:${data.aws_caller_identity.current.account_id}:secret:launchpad/${each.key}/engagecloud-agency-id" },
        { name = "ENGAGE_CLOUD_WEBHOOK_SECRET", valueFrom = "arn:aws:secretsmanager:${each.key}:${data.aws_caller_identity.current.account_id}:secret:launchpad/${each.key}/engagecloud-webhook-secret" },
        { name = "ENGAGECLOUD_FROM_NUMBERS", valueFrom = "arn:aws:secretsmanager:${each.key}:${data.aws_caller_identity.current.account_id}:secret:launchpad/${each.key}/engagecloud-from-numbers" },
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = "/ecs/${each.value.ecs_cluster}"
          "awslogs-region"        = each.key
          "awslogs-stream-prefix" = "ecs"
        }
      }

      healthCheck = {
        command     = ["CMD-SHELL", "curl -f http://localhost:8000/health || exit 1"]
        interval    = 30
        timeout     = 5
        retries     = 3
        startPeriod = 60
      }
    }
  ])

  tags = {
    Name        = "launchpad-backend-${each.key}"
    Region      = each.key
    Environment = var.environment
  }
}

# --- ECS Services (per region) ---

resource "aws_ecs_service" "backend" {
  for_each = { for k, v in var.regions : k => v if v.enabled }

  name            = each.value.ecs_service
  cluster         = aws_ecs_cluster.regional[each.key].id
  task_definition = aws_ecs_task_definition.backend[each.key].arn
  desired_count   = each.value.desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = var.private_subnet_ids
    security_groups  = [aws_security_group.ecs.id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.backend[each.key].arn
    container_name   = "backend-api"
    container_port   = 8000
  }

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  deployment_maximum_percent         = 200
  deployment_minimum_healthy_percent = 100

  lifecycle {
    ignore_changes = [desired_count]
  }

  depends_on = [aws_lb_listener.https]

  tags = {
    Name        = each.value.ecs_service
    Region      = each.key
    Environment = var.environment
  }
}

# --- Application Load Balancers (per region) ---

resource "aws_lb" "regional" {
  for_each = { for k, v in var.regions : k => v if v.enabled }

  name               = "launchpad-alb-${each.key}"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = var.public_subnet_ids

  enable_deletion_protection = each.value.is_primary

  access_logs {
    bucket  = aws_s3_bucket.alb_logs.id
    prefix  = "alb-${each.key}"
    enabled = true
  }

  tags = {
    Name        = "launchpad-alb-${each.key}"
    Region      = each.key
    Environment = var.environment
  }
}

resource "aws_lb_target_group" "backend" {
  for_each = { for k, v in var.regions : k => v if v.enabled }

  name        = "launchpad-tg-${each.key}"
  port        = 8000
  protocol    = "HTTP"
  vpc_id      = var.vpc_id
  target_type = "ip"

  health_check {
    enabled             = true
    healthy_threshold   = 2
    unhealthy_threshold = 3
    timeout             = 5
    interval            = 30
    path                = "/health"
    matcher             = "200"
  }

  deregistration_delay = 30

  tags = {
    Name        = "launchpad-tg-${each.key}"
    Region      = each.key
    Environment = var.environment
  }
}

resource "aws_lb_listener" "https" {
  for_each = { for k, v in var.regions : k => v if v.enabled }

  load_balancer_arn = aws_lb.regional[each.key].arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = aws_acm_certificate.launchpad.arn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.backend[each.key].arn
  }

  tags = {
    Name        = "launchpad-https-${each.key}"
    Region      = each.key
    Environment = var.environment
  }
}

resource "aws_lb_listener" "http_redirect" {
  for_each = { for k, v in var.regions : k => v if v.enabled }

  load_balancer_arn = aws_lb.regional[each.key].arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type = "redirect"
    redirect {
      port        = "443"
      protocol    = "HTTPS"
      status_code = "HTTP_301"
    }
  }

  tags = {
    Name        = "launchpad-http-redirect-${each.key}"
    Region      = each.key
    Environment = var.environment
  }
}

# --- IAM Roles (shared) ---

data "aws_caller_identity" "current" {}

resource "aws_iam_role" "ecs_execution" {
  name = "launchpad-ecs-execution-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "ecs-tasks.amazonaws.com"
        }
      }
    ]
  })

  tags = {
    Name        = "launchpad-ecs-execution-role"
    Environment = var.environment
  }
}

resource "aws_iam_role_policy_attachment" "ecs_execution" {
  role       = aws_iam_role.ecs_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "ecs_execution_secrets" {
  name = "launchpad-ecs-secrets"
  role = aws_iam_role.ecs_execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "secretsmanager:GetSecretValue",
        ]
        Resource = [
          "arn:aws:secretsmanager:*:${data.aws_caller_identity.current.account_id}:secret:launchpad/*",
        ]
      }
    ]
  })
}

resource "aws_iam_role" "ecs_task" {
  name = "launchpad-ecs-task-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "ecs-tasks.amazonaws.com"
        }
      }
    ]
  })

  tags = {
    Name        = "launchpad-ecs-task-role"
    Environment = var.environment
  }
}

resource "aws_iam_role_policy" "ecs_task_s3" {
  name = "launchpad-ecs-task-s3"
  role = aws_iam_role.ecs_task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:ListBucket",
        ]
        Resource = [
          aws_s3_bucket.backups_primary.arn,
          "${aws_s3_bucket.backups_primary.arn}/*",
        ]
      }
    ]
  })
}

# --- S3 Buckets ---

resource "aws_s3_bucket" "backups_primary" {
  bucket = "launchpad-backups-${var.environment}-primary"

  tags = {
    Name        = "launchpad-backups-primary"
    Region      = "us-east-1"
    Environment = var.environment
  }
}

resource "aws_s3_bucket_versioning" "backups_primary" {
  bucket = aws_s3_bucket.backups_primary.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket" "alb_logs" {
  bucket = "launchpad-alb-logs-${var.environment}"

  tags = {
    Name        = "launchpad-alb-logs"
    Environment = var.environment
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "alb_logs" {
  bucket = aws_s3_bucket.alb_logs.id

  rule {
    id     = "expire-old-logs"
    status = "Enabled"
    expiration {
      days = 90
    }
  }
}

# --- Security Groups ---

resource "aws_security_group" "alb" {
  name_prefix = "launchpad-alb-"
  vpc_id      = var.vpc_id

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
    Name        = "launchpad-alb-sg"
    Environment = var.environment
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_security_group" "ecs" {
  name_prefix = "launchpad-ecs-"
  vpc_id      = var.vpc_id

  ingress {
    from_port       = 8000
    to_port         = 8000
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name        = "launchpad-ecs-sg"
    Environment = var.environment
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_security_group" "rds" {
  name_prefix = "launchpad-rds-"
  vpc_id      = var.vpc_id

  ingress {
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.ecs.id]
  }

  tags = {
    Name        = "launchpad-rds-sg"
    Environment = var.environment
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_security_group" "redis" {
  name_prefix = "launchpad-redis-"
  vpc_id      = var.vpc_id

  ingress {
    from_port       = 6379
    to_port         = 6379
    protocol        = "tcp"
    security_groups = [aws_security_group.ecs.id]
  }

  tags = {
    Name        = "launchpad-redis-sg"
    Environment = var.environment
  }

  lifecycle {
    create_before_destroy = true
  }
}

# --- ACM Certificate ---

resource "aws_acm_certificate" "launchpad" {
  domain_name       = "*.launchpad.com"
  validation_method = "DNS"

  subject_alternative_names = [
    "launchpad.com",
    "api.launchpad.com",
    "app.launchpad.com",
    "api-eu.launchpad.com",
    "api-ap.launchpad.com",
  ]

  lifecycle {
    create_before_destroy = true
  }

  tags = {
    Name        = "launchpad-certificate"
    Environment = var.environment
  }
}
