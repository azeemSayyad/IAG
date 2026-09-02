# CloudFront CDN (Phase 50.4)
#
# Serves frontend static assets globally with low latency.
#
# Architecture:
#   app.launchpad.com → CloudFront → S3 (frontend static files)
#   app.launchpad.com/api/* → CloudFront → regional ALB (API passthrough)
#
# Features:
# - HTTP/2 and HTTP/3 support
# - Gzip/Brotli compression
# - WAF integration
# - Custom domain with ACM certificate
# - Cache policies for static assets and API responses
# - Origin failover (primary → secondary region)

# --- S3 Bucket for Frontend Static Assets ---

resource "aws_s3_bucket" "frontend" {
  bucket = "launchpad-frontend-${var.environment}"

  tags = {
    Name        = "launchpad-frontend"
    Environment = var.environment
  }
}

resource "aws_s3_bucket_versioning" "frontend" {
  bucket = aws_s3_bucket.frontend.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "frontend" {
  bucket = aws_s3_bucket.frontend.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "frontend" {
  bucket = aws_s3_bucket.frontend.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# --- S3 Bucket Policy for CloudFront OAC ---

resource "aws_s3_bucket_policy" "frontend" {
  bucket = aws_s3_bucket.frontend.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "AllowCloudFrontOAC"
        Effect    = "Allow"
        Principal = {
          Service = "cloudfront.amazonaws.com"
        }
        Action   = "s3:GetObject"
        Resource = "${aws_s3_bucket.frontend.arn}/*"
        Condition = {
          StringEquals = {
            "AWS:SourceArn" = aws_cloudfront_distribution.frontend.arn
          }
        }
      }
    ]
  })
}

# --- CloudFront Origin Access Control ---

resource "aws_cloudfront_origin_access_control" "frontend" {
  name                              = "launchpad-frontend-oac"
  description                       = "OAC for Launchpad frontend S3 bucket"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

# --- CloudFront Cache Policy (Static Assets) ---

resource "aws_cloudfront_cache_policy" "static_assets" {
  name        = "launchpad-static-assets"
  comment     = "Cache policy for static assets (JS, CSS, images)"
  default_ttl = 86400    # 24 hours
  max_ttl     = 31536000 # 1 year
  min_ttl     = 0

  parameters_in_cache_key_and_forwarded_to_origin {
    enable_accept_encoding_brotli = true
    enable_accept_encoding_gzip   = true

    cookies_config {
      cookie_behavior = "none"
    }

    headers_config {
      header_behavior = "none"
    }

    query_strings_config {
      query_string_behavior = "none"
    }
  }
}

# --- CloudFront Cache Policy (API Passthrough) ---

resource "aws_cloudfront_cache_policy" "api_passthrough" {
  name        = "launchpad-api-passthrough"
  comment     = "No caching for API requests"
  default_ttl = 0
  max_ttl     = 0
  min_ttl     = 0

  parameters_in_cache_key_and_forwarded_to_origin {
    enable_accept_encoding_brotli = false
    enable_accept_encoding_gzip   = false

    cookies_config {
      cookie_behavior = "all"
    }

    headers_config {
      header_behavior = "whitelist"
      headers {
        items = [
          "Authorization",
          "Content-Type",
          "Accept",
          "Origin",
          "Referer",
        ]
      }
    }

    query_strings_config {
      query_string_behavior = "all"
    }
  }
}

# --- CloudFront Response Headers Policy ---

resource "aws_cloudfront_response_headers_policy" "security_headers" {
  name    = "launchpad-security-headers"
  comment = "Security headers for Launchpad frontend"

  security_headers_config {
    content_type_options {
      override = true
    }

    frame_options {
      frame_option = "DENY"
      override     = true
    }

    referrer_policy {
      referrer_policy = "strict-origin-when-cross-origin"
      override        = true
    }

    strict_transport_security {
      access_control_max_age_sec = 31536000
      include_subdomains         = true
      preload                    = true
      override                   = true
    }

    xss_protection {
      mode_block = true
      protection = true
      override   = true
    }
  }

  custom_headers_config {
    items {
      header   = "Permissions-Policy"
      value    = "camera=(), microphone=(), geolocation=()"
      override = true
    }
  }
}

# --- CloudFront Distribution ---

resource "aws_cloudfront_distribution" "frontend" {
  enabled             = true
  is_ipv6_enabled     = true
  comment             = "Launchpad Call Center Frontend"
  default_root_object = "index.html"
  price_class         = var.cloudfront_price_class
  aliases             = ["app.launchpad.com"]
  web_acl_id          = aws_wafv2_web_acl.cloudfront.arn
  http_version        = "http2and3"
  retain_on_delete    = false
  wait_for_deployment = true

  # --- S3 Origin (Static Assets) ---
  origin {
    domain_name              = aws_s3_bucket.frontend.bucket_regional_domain_name
    origin_id                = "S3-frontend"
    origin_access_control_id = aws_cloudfront_origin_access_control.frontend.id
  }

  # --- ALB Origin (API Passthrough) ---
  origin {
    domain_name = aws_lb.regional["us-east-1"].dns_name
    origin_id   = "ALB-primary"

    custom_origin_config {
      http_port              = 80
      https_port             = 443
      origin_protocol_policy = "https-only"
      origin_ssl_protocols   = ["TLSv1.2"]
      origin_read_timeout    = 60
      origin_keepalive_timeout = 5
    }
  }

  # --- ALB Origin (EU Fallback) ---
  origin {
    domain_name = aws_lb.regional["eu-west-1"].dns_name
    origin_id   = "ALB-eu"

    custom_origin_config {
      http_port              = 80
      https_port             = 443
      origin_protocol_policy = "https-only"
      origin_ssl_protocols   = ["TLSv1.2"]
      origin_read_timeout    = 60
      origin_keepalive_timeout = 5
    }
  }

  # --- Default Behavior (Static Assets) ---
  default_cache_behavior {
    allowed_methods            = ["GET", "HEAD", "OPTIONS"]
    cached_methods             = ["GET", "HEAD"]
    target_origin_id           = "S3-frontend"
    viewer_protocol_policy     = "redirect-to-https"
    compress                   = true
    cache_policy_id            = aws_cloudfront_cache_policy.static_assets.id
    response_headers_policy_id = aws_cloudfront_response_headers_policy.security_headers.id

    function_association {
      event_type   = "viewer-request"
      function_arn = aws_cloudfront_function.rewrite.arn
    }
  }

  # --- API Behavior (Passthrough to ALB) ---
  ordered_cache_behavior {
    path_pattern               = "/api/*"
    allowed_methods            = ["DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"]
    cached_methods             = ["GET", "HEAD"]
    target_origin_id           = "ALB-primary"
    viewer_protocol_policy     = "redirect-to-https"
    compress                   = true
    cache_policy_id            = aws_cloudfront_cache_policy.api_passthrough.id
    response_headers_policy_id = aws_cloudfront_response_headers_policy.security_headers.id
    origin_request_policy_id   = aws_cloudfront_origin_request_policy.api.id

    # Failover to EU if primary is down
    origin_group {
      failover_criteria {
        status_codes = [500, 502, 503, 504]
      }

      member {
        origin_id = "ALB-primary"
      }

      member {
        origin_id = "ALB-eu"
      }
    }
  }

  # --- WebSocket Behavior (Passthrough) ---
  ordered_cache_behavior {
    path_pattern             = "/socket.io/*"
    allowed_methods          = ["DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"]
    cached_methods           = ["GET", "HEAD"]
    target_origin_id         = "ALB-primary"
    viewer_protocol_policy   = "redirect-to-https"
    compress                 = false
    cache_policy_id          = aws_cloudfront_cache_policy.api_passthrough.id
    origin_request_policy_id = aws_cloudfront_origin_request_policy.api.id
  }

  # --- Static Assets Behavior (Long Cache) ---
  ordered_cache_behavior {
    path_pattern           = "/_next/static/*"
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]
    target_origin_id       = "S3-frontend"
    viewer_protocol_policy = "redirect-to-https"
    compress               = true
    cache_policy_id        = aws_cloudfront_cache_policy.static_assets.id
  }

  # --- SPA Fallback (return index.html for all routes) ---
  custom_error_response {
    error_code            = 403
    response_code         = 200
    response_page_path    = "/index.html"
    error_caching_min_ttl = 0
  }

  custom_error_response {
    error_code            = 404
    response_code         = 200
    response_page_path    = "/index.html"
    error_caching_min_ttl = 0
  }

  # --- SSL Certificate ---
  viewer_certificate {
    acm_certificate_arn      = aws_acm_certificate.launchpad.arn
    ssl_support_method       = "sni-only"
    minimum_protocol_version = "TLSv1.2_2021"
  }

  # --- Geo Restrictions ---
  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  tags = {
    Name        = "launchpad-frontend-cdn"
    Environment = var.environment
  }
}

# --- CloudFront Function (SPA Rewrite) ---

resource "aws_cloudfront_function" "rewrite" {
  name    = "launchpad-spa-rewrite"
  runtime = "cloudfront-js-2.0"
  comment = "Rewrite requests to index.html for SPA routing"
  publish = true

  code = <<-JS
    function handler(event) {
      var request = event.request;
      var uri = request.uri;

      // If the URI has a file extension, pass through
      if (uri.match(/\.[a-z]{2,4}$/)) {
        return request;
      }

      // Otherwise, rewrite to index.html for SPA routing
      request.uri = '/index.html';
      return request;
    }
  JS
}

# --- CloudFront Origin Request Policy (API) ---

resource "aws_cloudfront_origin_request_policy" "api" {
  name    = "launchpad-api-origin-request"
  comment = "Forward necessary headers for API requests"

  cookies_config {
    cookie_behavior = "all"
  }

  headers_config {
    header_behavior = "whitelist"
    headers {
      items = [
        "Authorization",
        "Content-Type",
        "Accept",
        "Origin",
        "Referer",
        "X-Forwarded-For",
        "X-Real-IP",
      ]
    }
  }

  query_strings_config {
    query_string_behavior = "all"
  }
}

# --- WAF for CloudFront ---

resource "aws_wafv2_web_acl" "cloudfront" {
  provider = aws.primary

  name        = "launchpad-cloudfront-waf"
  description = "WAF for Launchpad CloudFront distribution"
  scope       = "CLOUDFRONT"

  default_action {
    allow {}
  }

  # Rate limiting
  rule {
    name     = "RateLimitRule"
    priority = 1

    override_action {
      none {}
    }

    statement {
      rate_based_statement {
        limit              = 2000
        aggregate_key_type = "IP"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "CloudFrontRateLimit"
      sampled_requests_enabled   = true
    }

    action {
      block {}
    }
  }

  # AWS Managed Rules - Common
  rule {
    name     = "AWSManagedRulesCommonRuleSet"
    priority = 2

    override_action {
      none {}
    }

    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesCommonRuleSet"
        vendor_name = "AWS"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "CloudFrontCommonRules"
      sampled_requests_enabled   = true
    }
  }

  # AWS Managed Rules - SQL Injection
  rule {
    name     = "AWSManagedRulesSQLiRuleSet"
    priority = 3

    override_action {
      none {}
    }

    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesSQLiRuleSet"
        vendor_name = "AWS"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "CloudFrontSQLiRules"
      sampled_requests_enabled   = true
    }
  }

  visibility_config {
    cloudwatch_metrics_enabled = true
    metric_name                = "launchpad-cloudfront-waf"
    sampled_requests_enabled   = true
  }

  tags = {
    Name        = "launchpad-cloudfront-waf"
    Environment = var.environment
  }
}

# --- S3 Cross-Region Replication (Frontend Assets) ---

resource "aws_s3_bucket" "frontend_replica" {
  provider = aws.eu_west

  bucket = "launchpad-frontend-${var.environment}-eu"

  tags = {
    Name        = "launchpad-frontend-eu"
    Region      = "eu-west-1"
    Environment = var.environment
  }
}

resource "aws_s3_bucket_versioning" "frontend_replica" {
  provider = aws.eu_west

  bucket = aws_s3_bucket.frontend_replica.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_replication_configuration" "frontend" {
  role   = aws_iam_role.s3_replication.arn
  bucket = aws_s3_bucket.frontend.id

  rule {
    id     = "replicate-to-eu"
    status = "Enabled"

    destination {
      bucket        = aws_s3_bucket.frontend_replica.arn
      storage_class = "STANDARD"

      encryption_configuration {
        replica_kms_key_id = "alias/aws/s3"
      }
    }

    source_selection_criteria {
      sse_kms_encrypted_objects {
        status = "Enabled"
      }
    }
  }
}

# --- IAM Role for S3 Replication ---

resource "aws_iam_role" "s3_replication" {
  name = "launchpad-s3-replication"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "s3.amazonaws.com"
        }
      }
    ]
  })

  tags = {
    Name        = "launchpad-s3-replication"
    Environment = var.environment
  }
}

resource "aws_iam_role_policy" "s3_replication" {
  name = "launchpad-s3-replication-policy"
  role = aws_iam_role.s3_replication.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:GetReplicationConfiguration",
          "s3:ListBucket",
        ]
        Resource = aws_s3_bucket.frontend.arn
      },
      {
        Effect = "Allow"
        Action = [
          "s3:GetObjectVersionForReplication",
          "s3:GetObjectVersionAcl",
          "s3:GetObjectVersionTagging",
        ]
        Resource = "${aws_s3_bucket.frontend.arn}/*"
      },
      {
        Effect = "Allow"
        Action = [
          "s3:ReplicateObject",
          "s3:ReplicateDelete",
          "s3:ReplicateTags",
        ]
        Resource = "${aws_s3_bucket.frontend_replica.arn}/*"
      }
    ]
  })
}

# --- Outputs ---

output "cloudfront_distribution_id" {
  description = "CloudFront distribution ID"
  value       = aws_cloudfront_distribution.frontend.id
}

output "cloudfront_domain" {
  description = "CloudFront distribution domain name"
  value       = aws_cloudfront_distribution.frontend.domain_name
}

output "frontend_s3_bucket" {
  description = "S3 bucket for frontend static assets"
  value       = aws_s3_bucket.frontend.bucket
}
