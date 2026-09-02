# AWS Provider Configuration (Phase 50.1)
#
# Multi-region provider setup for global deployment.
# Each region has its own provider alias for region-specific resources.

terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  backend "s3" {
    bucket         = "launchpad-terraform-state"
    key            = "global/terraform.tfstate"
    region         = "us-east-1"
    dynamodb_table = "terraform-locks"
    encrypt        = true
  }
}

# --- Primary Region (us-east-1) ---

provider "aws" {
  region = "us-east-1"
  alias  = "primary"

  default_tags {
    tags = {
      Project     = "launchpad"
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}

# --- Europe Region (eu-west-1) ---

provider "aws" {
  region = "eu-west-1"
  alias  = "eu_west"

  default_tags {
    tags = {
      Project     = "launchpad"
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}

# --- Asia Pacific Region (ap-southeast-1) ---

provider "aws" {
  region = "ap-southeast-1"
  alias  = "ap_southeast"

  default_tags {
    tags = {
      Project     = "launchpad"
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}
