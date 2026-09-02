variable "environment" {
  description = "Deployment environment"
  type        = string
  default     = "production"
}

variable "region" {
  description = "Primary AWS region"
  type        = string
  default     = "us-east-1"
}

variable "db_password" {
  description = "RDS master password"
  type        = string
  sensitive   = true
}

# --- Primary Region (us-east-1) ---

variable "vpc_id" {
  description = "VPC ID for primary region (us-east-1)"
  type        = string
}

variable "private_subnet_ids" {
  description = "Private subnet IDs for primary region (us-east-1)"
  type        = list(string)
}

variable "public_subnet_ids" {
  description = "Public subnet IDs for primary region (us-east-1)"
  type        = list(string)
}

# --- Europe Region (eu-west-1) ---

variable "eu_vpc_id" {
  description = "VPC ID for Europe region (eu-west-1)"
  type        = string
}

variable "eu_private_subnet_ids" {
  description = "Private subnet IDs for Europe region (eu-west-1)"
  type        = list(string)
}

variable "eu_public_subnet_ids" {
  description = "Public subnet IDs for Europe region (eu-west-1)"
  type        = list(string)
}

# --- Asia Pacific Region (ap-southeast-1) ---

variable "ap_vpc_id" {
  description = "VPC ID for Asia Pacific region (ap-southeast-1)"
  type        = string
}

variable "ap_private_subnet_ids" {
  description = "Private subnet IDs for Asia Pacific region (ap-southeast-1)"
  type        = list(string)
}

variable "ap_public_subnet_ids" {
  description = "Public subnet IDs for Asia Pacific region (ap-southeast-1)"
  type        = list(string)
}

# --- Domain Configuration ---

variable "domain_name" {
  description = "Primary domain name"
  type        = string
  default     = "launchpad.com"
}

variable "api_domain" {
  description = "API domain name"
  type        = string
  default     = "api.launchpad.com"
}

variable "app_domain" {
  description = "Frontend application domain"
  type        = string
  default     = "app.launchpad.com"
}

# --- CloudFront ---

variable "cloudfront_price_class" {
  description = "CloudFront price class"
  type        = string
  default     = "PriceClass_100"  # US, Canada, Europe
}

# --- S3 Replication ---

variable "enable_cross_region_replication" {
  description = "Enable cross-region S3 replication for backups"
  type        = bool
  default     = true
}

# --- Failover ---

variable "enable_automatic_failover" {
  description = "Enable automatic database failover"
  type        = bool
  default     = true
}
