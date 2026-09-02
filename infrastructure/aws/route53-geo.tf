# Route 53 Geo Routing (Phase 50.3)
#
# Latency-based routing directs users to the closest regional endpoint.
# Health checks enable automatic failover when a region goes down.
#
# DNS Architecture:
#   api.launchpad.com → latency-based → api-{region}.launchpad.com → regional ALB
#   app.launchpad.com → CloudFront distribution
#   ws.launchpad.com  → latency-based → regional ALB (WebSocket)

# --- Health Checks (per region) ---

resource "aws_route53_health_check" "primary_api" {
  fqdn              = "api-us.launchpad.com"
  port               = 443
  type               = "HTTPS"
  resource_path      = "/health"
  failure_threshold  = 3
  request_interval   = 10
  measure_latency    = true
  regions            = ["us-east-1", "us-west-2", "eu-west-1"]

  tags = {
    Name        = "launchpad-health-check-primary"
    Region      = "us-east-1"
    Environment = var.environment
  }
}

resource "aws_route53_health_check" "eu_api" {
  fqdn              = "api-eu.launchpad.com"
  port               = 443
  type               = "HTTPS"
  resource_path      = "/health"
  failure_threshold  = 3
  request_interval   = 10
  measure_latency    = true
  regions            = ["eu-west-1", "eu-central-1", "us-east-1"]

  tags = {
    Name        = "launchpad-health-check-eu"
    Region      = "eu-west-1"
    Environment = var.environment
  }
}

resource "aws_route53_health_check" "ap_api" {
  fqdn              = "api-ap.launchpad.com"
  port               = 443
  type               = "HTTPS"
  resource_path      = "/health"
  failure_threshold  = 3
  request_interval   = 10
  measure_latency    = true
  regions            = ["ap-southeast-1", "ap-northeast-1", "us-west-2"]

  tags = {
    Name        = "launchpad-health-check-ap"
    Region      = "ap-southeast-1"
    Environment = var.environment
  }
}

# --- Route 53 Hosted Zone ---

data "aws_route53_zone" "launchpad" {
  name         = "launchpad.com"
  private_zone = false
}

# --- API Latency-Based Routing ---

resource "aws_route53_record" "api_primary" {
  zone_id = data.aws_route53_zone.launchpad.zone_id
  name    = "api.launchpad.com"
  type    = "A"

  alias {
    name                   = aws_lb.regional["us-east-1"].dns_name
    zone_id                = aws_lb.regional["us-east-1"].zone_id
    evaluate_target_health = true
  }

  latency_routing_policy {
    region = "us-east-1"
  }

  set_identifier  = "primary-us-east-1"
  health_check_id = aws_route53_health_check.primary_api.id
}

resource "aws_route53_record" "api_eu" {
  zone_id = data.aws_route53_zone.launchpad.zone_id
  name    = "api.launchpad.com"
  type    = "A"

  alias {
    name                   = aws_lb.regional["eu-west-1"].dns_name
    zone_id                = aws_lb.regional["eu-west-1"].zone_id
    evaluate_target_health = true
  }

  latency_routing_policy {
    region = "eu-west-1"
  }

  set_identifier  = "secondary-eu-west-1"
  health_check_id = aws_route53_health_check.eu_api.id
}

resource "aws_route53_record" "api_ap" {
  zone_id = data.aws_route53_zone.launchpad.zone_id
  name    = "api.launchpad.com"
  type    = "A"

  alias {
    name                   = aws_lb.regional["ap-southeast-1"].dns_name
    zone_id                = aws_lb.regional["ap-southeast-1"].zone_id
    evaluate_target_health = true
  }

  latency_routing_policy {
    region = "ap-southeast-1"
  }

  set_identifier  = "secondary-ap-southeast-1"
  health_check_id = aws_route53_health_check.ap_api.id
}

# --- WebSocket Latency-Based Routing ---

resource "aws_route53_record" "ws_primary" {
  zone_id = data.aws_route53_zone.launchpad.zone_id
  name    = "ws.launchpad.com"
  type    = "A"

  alias {
    name                   = aws_lb.regional["us-east-1"].dns_name
    zone_id                = aws_lb.regional["us-east-1"].zone_id
    evaluate_target_health = true
  }

  latency_routing_policy {
    region = "us-east-1"
  }

  set_identifier  = "ws-us-east-1"
  health_check_id = aws_route53_health_check.primary_api.id
}

resource "aws_route53_record" "ws_eu" {
  zone_id = data.aws_route53_zone.launchpad.zone_id
  name    = "ws.launchpad.com"
  type    = "A"

  alias {
    name                   = aws_lb.regional["eu-west-1"].dns_name
    zone_id                = aws_lb.regional["eu-west-1"].zone_id
    evaluate_target_health = true
  }

  latency_routing_policy {
    region = "eu-west-1"
  }

  set_identifier  = "ws-eu-west-1"
  health_check_id = aws_route53_health_check.eu_api.id
}

resource "aws_route53_record" "ws_ap" {
  zone_id = data.aws_route53_zone.launchpad.zone_id
  name    = "ws.launchpad.com"
  type    = "A"

  alias {
    name                   = aws_lb.regional["ap-southeast-1"].dns_name
    zone_id                = aws_lb.regional["ap-southeast-1"].zone_id
    evaluate_target_health = true
  }

  latency_routing_policy {
    region = "ap-southeast-1"
  }

  set_identifier  = "ws-ap-southeast-1"
  health_check_id = aws_route53_health_check.ap_api.id
}

# --- Regional API Direct Access (for debugging / admin) ---

resource "aws_route53_record" "api_us_direct" {
  zone_id = data.aws_route53_zone.launchpad.zone_id
  name    = "api-us.launchpad.com"
  type    = "A"

  alias {
    name                   = aws_lb.regional["us-east-1"].dns_name
    zone_id                = aws_lb.regional["us-east-1"].zone_id
    evaluate_target_health = true
  }
}

resource "aws_route53_record" "api_eu_direct" {
  zone_id = data.aws_route53_zone.launchpad.zone_id
  name    = "api-eu.launchpad.com"
  type    = "A"

  alias {
    name                   = aws_lb.regional["eu-west-1"].dns_name
    zone_id                = aws_lb.regional["eu-west-1"].zone_id
    evaluate_target_health = true
  }
}

resource "aws_route53_record" "api_ap_direct" {
  zone_id = data.aws_route53_zone.launchpad.zone_id
  name    = "api-ap.launchpad.com"
  type    = "A"

  alias {
    name                   = aws_lb.regional["ap-southeast-1"].dns_name
    zone_id                = aws_lb.regional["ap-southeast-1"].zone_id
    evaluate_target_health = true
  }
}

# --- Failover Routing (primary → secondary on failure) ---

resource "aws_route53_record" "api_failover_primary" {
  zone_id = data.aws_route53_zone.launchpad.zone_id
  name    = "api-failover.launchpad.com"
  type    = "A"

  alias {
    name                   = aws_lb.regional["us-east-1"].dns_name
    zone_id                = aws_lb.regional["us-east-1"].zone_id
    evaluate_target_health = true
  }

  failover_routing_policy {
    type = "PRIMARY"
  }

  set_identifier  = "failover-primary"
  health_check_id = aws_route53_health_check.primary_api.id
}

resource "aws_route53_record" "api_failover_secondary" {
  zone_id = data.aws_route53_zone.launchpad.zone_id
  name    = "api-failover.launchpad.com"
  type    = "A"

  alias {
    name                   = aws_lb.regional["eu-west-1"].dns_name
    zone_id                = aws_lb.regional["eu-west-1"].zone_id
    evaluate_target_health = true
  }

  failover_routing_policy {
    type = "SECONDARY"
  }

  set_identifier  = "failover-secondary-eu"
  health_check_id = aws_route53_health_check.eu_api.id
}

# --- Frontend (CloudFront) ---

resource "aws_route53_record" "app" {
  zone_id = data.aws_route53_zone.launchpad.zone_id
  name    = "app.launchpad.com"
  type    = "A"

  alias {
    name                   = aws_cloudfront_distribution.frontend.domain_name
    zone_id                = aws_cloudfront_distribution.frontend.hosted_zone_id
    evaluate_target_health = false
  }
}

# --- Outputs ---

output "api_endpoint" {
  description = "Main API endpoint with latency-based routing"
  value       = "api.launchpad.com"
}

output "ws_endpoint" {
  description = "WebSocket endpoint with latency-based routing"
  value       = "ws.launchpad.com"
}

output "app_endpoint" {
  description = "Frontend endpoint via CloudFront"
  value       = "app.launchpad.com"
}

output "health_check_ids" {
  description = "Health check IDs per region"
  value = {
    primary = aws_route53_health_check.primary_api.id
    eu      = aws_route53_health_check.eu_api.id
    ap      = aws_route53_health_check.ap_api.id
  }
}
