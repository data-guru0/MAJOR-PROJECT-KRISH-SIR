terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.region
}

module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "~> 5.0"

  name = "${var.cluster_name}-vpc"
  cidr = "10.0.0.0/16"

  azs            = ["${var.region}a", "${var.region}b"]
  public_subnets = ["10.0.1.0/24", "10.0.2.0/24"]

  enable_nat_gateway = false
  enable_dns_hostnames = true
  enable_dns_support   = true

  tags = {
    Environment = var.environment
  }
}

module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "~> 20.0"

  cluster_name    = var.cluster_name
  cluster_version = "1.29"

  vpc_id     = module.vpc.vpc_id
  subnet_ids = module.vpc.public_subnets

  cluster_endpoint_public_access = true

  eks_managed_node_groups = {
    default = {
      instance_types = ["t3.medium"]

      min_size     = 1
      max_size     = 3
      desired_size = 2
    }
  }

  tags = {
    Environment = var.environment
  }
}

resource "aws_db_subnet_group" "main" {
  name       = "${var.cluster_name}-db-subnet"
  subnet_ids = module.vpc.public_subnets
}

resource "aws_db_instance" "postgres" {
  identifier        = "${var.cluster_name}-postgres"
  engine            = "postgres"
  engine_version    = "15"
  instance_class    = "db.t3.micro"
  db_name           = "codereviewer"
  username          = "user"
  password          = var.db_password
  multi_az          = false
  publicly_accessible = false
  skip_final_snapshot = true

  db_subnet_group_name = aws_db_subnet_group.main.name

  tags = {
    Environment = var.environment
  }
}

resource "aws_elasticache_subnet_group" "main" {
  name       = "${var.cluster_name}-cache-subnet"
  subnet_ids = module.vpc.public_subnets
}

resource "aws_elasticache_cluster" "redis" {
  cluster_id           = "${var.cluster_name}-redis"
  engine               = "redis"
  engine_version       = "7.0"
  node_type            = "cache.t3.micro"
  num_cache_nodes      = 1
  parameter_group_name = "default.redis7"

  subnet_group_name = aws_elasticache_subnet_group.main.name

  tags = {
    Environment = var.environment
  }
}

resource "aws_s3_bucket" "reports" {
  bucket = "ai-code-reviewer-reports"

  tags = {
    Environment = var.environment
  }
}

resource "aws_ecr_repository" "gateway" {
  name                 = "gateway"
  image_tag_mutability = "MUTABLE"

  tags = {
    Environment = var.environment
  }
}

resource "aws_ecr_repository" "webhook" {
  name                 = "webhook"
  image_tag_mutability = "MUTABLE"

  tags = {
    Environment = var.environment
  }
}

resource "aws_ecr_repository" "orchestrator" {
  name                 = "orchestrator"
  image_tag_mutability = "MUTABLE"

  tags = {
    Environment = var.environment
  }
}

resource "aws_ecr_repository" "reviewer" {
  name                 = "reviewer"
  image_tag_mutability = "MUTABLE"

  tags = {
    Environment = var.environment
  }
}

resource "aws_ecr_repository" "learner" {
  name                 = "learner"
  image_tag_mutability = "MUTABLE"

  tags = {
    Environment = var.environment
  }
}
