terraform {
  required_version = ">= 1.6.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws",
      version = ">= 5.40"
    }
    random = {
      source  = "hashicorp/random",
      version = ">= 3.6"
    }
    archive = {
      source  = "hashicorp/archive",
      version = ">= 2.4"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

provider "aws" {
  alias = "use1"
  region = "us-east-1"
}

data "aws_partition" "current" {}
data "aws_caller_identity" "current" {}

locals {
  partition                  = data.aws_partition.current.partition
  bedrock_model_arn_wildcard = "arn:${local.partition}:bedrock:*::foundation-model/*"
  bedrock_sonnet_inference_profile_arn = "arn:${local.partition}:bedrock:${var.aws_region}:${data.aws_caller_identity.current.account_id}:inference-profile/eu.anthropic.claude-sonnet-4-5-20250929-v1:0"
  bedrock_sonnet_inference_profile_name = "eu.anthropic.claude-sonnet-4-5-20250929-v1"
}

resource "random_id" "suffix" { byte_length = 4 }

# --- S3 bucket ---
resource "aws_s3_bucket" "uploads" {
  bucket = "${var.name_prefix}-uploads-${random_id.suffix.hex}"
}
resource "aws_s3_bucket_public_access_block" "uploads" {
  bucket                  = aws_s3_bucket.uploads.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
resource "aws_s3_bucket_server_side_encryption_configuration" "uploads" {
  bucket = aws_s3_bucket.uploads.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}
resource "aws_s3_bucket_cors_configuration" "uploads" {
  bucket = aws_s3_bucket.uploads.id
  cors_rule {
    allowed_methods = ["PUT", "GET", "HEAD"]
    allowed_origins = var.cors_allowed_origins
    allowed_headers = ["*"]
    expose_headers  = ["ETag"]
    max_age_seconds = 3000
  }
}
resource "aws_s3_bucket_lifecycle_configuration" "uploads" {
  bucket = aws_s3_bucket.uploads.id
  rule {
    id     = "expire-uploads"
    status = "Enabled"
    filter { prefix = "uploads/" }
    expiration { days = 7 }
  }
  rule {
    id     = "expire-textract-results"
    status = "Enabled"
    filter { prefix = "textract-results/" }
    expiration { days = 30 }
  }
}

# --- DynamoDB ---
resource "aws_dynamodb_table" "usage" {
  name         = "${var.name_prefix}-usage"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pk"
  range_key    = "sk"
  attribute {
    name = "pk"
    type = "S"
  }
  attribute {
    name = "sk"
    type = "S"
  }
}
resource "aws_dynamodb_table" "api_keys" {
  name         = "${var.name_prefix}-api-keys"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "api_key"
  attribute {
    name = "api_key"
    type = "S"
  }
}
resource "aws_dynamodb_table_item" "api_key_seed" {
  table_name = aws_dynamodb_table.api_keys.name
  hash_key   = aws_dynamodb_table.api_keys.hash_key
  item = jsonencode({
    api_key = { S = var.seed_api_key_value }
    tier    = { S = var.seed_api_key_tier }
  })
}

# --- SGs ---
resource "aws_security_group" "lambda_sg" {
  name   = "${var.name_prefix}-lambda-sg"
  vpc_id = var.vpc_id
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}
resource "aws_security_group" "endpoints_sg" {
  name   = "${var.name_prefix}-endpoints-sg"
  vpc_id = var.vpc_id
  ingress {
    from_port       = 443
    to_port         = 443
    protocol        = "tcp"
    security_groups = [aws_security_group.lambda_sg.id]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# --- VPC endpoints (no NAT) ---
resource "aws_vpc_endpoint" "s3" {
  vpc_id            = var.vpc_id
  service_name      = "com.amazonaws.${var.aws_region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = var.private_route_table_ids
}
resource "aws_vpc_endpoint" "dynamodb" {
  vpc_id            = var.vpc_id
  service_name      = "com.amazonaws.${var.aws_region}.dynamodb"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = var.private_route_table_ids
}
resource "aws_vpc_endpoint" "bedrock_runtime" {
  vpc_id              = var.vpc_id
  service_name        = "com.amazonaws.${var.aws_region}.bedrock-runtime"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = var.private_subnet_ids
  security_group_ids  = [aws_security_group.endpoints_sg.id]
  private_dns_enabled = true
}
resource "aws_vpc_endpoint" "logs" {
  vpc_id              = var.vpc_id
  service_name        = "com.amazonaws.${var.aws_region}.logs"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = var.private_subnet_ids
  security_group_ids  = [aws_security_group.endpoints_sg.id]
  private_dns_enabled = true
}

resource "aws_vpc_endpoint" "textract" {
  vpc_id              = var.vpc_id
  service_name        = "com.amazonaws.${var.aws_region}.textract"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = var.private_subnet_ids
  security_group_ids  = [aws_security_group.endpoints_sg.id]
  private_dns_enabled = true
}

# --- Textract async notification channel ---
resource "aws_sns_topic" "textract_notifications" {
  name = "${var.name_prefix}-textract-notifications"
}
resource "aws_iam_role" "textract_sns_role" {
  name = "${var.name_prefix}-textract-sns-role"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17",
    Statement = [{ Effect = "Allow", Principal = { Service = "textract.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
}
resource "aws_iam_policy" "textract_sns_publish" {
  name = "${var.name_prefix}-textract-sns-publish"
  policy = jsonencode({
    Version   = "2012-10-17",
    Statement = [{ Effect = "Allow", Action = ["sns:Publish"], Resource = aws_sns_topic.textract_notifications.arn }]
  })
}
resource "aws_iam_role_policy_attachment" "textract_sns_publish_attach" {
  role       = aws_iam_role.textract_sns_role.name
  policy_arn = aws_iam_policy.textract_sns_publish.arn
}

# --- IAM role for Lambda (app stack uses this) ---
resource "aws_iam_role" "lambda_role" {
  name = "${var.name_prefix}-lambda-role"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17",
    Statement = [{ Effect = "Allow", Principal = { Service = "lambda.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
}
resource "aws_iam_role_policy_attachment" "lambda_basic" {
  role       = aws_iam_role.lambda_role.name
  policy_arn = "arn:${local.partition}:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}
resource "aws_iam_policy" "lambda_app_policy" {
  name = "${var.name_prefix}-lambda-app-policy"
  policy = jsonencode({
    Version = "2012-10-17",
    Statement = [
      { Effect = "Allow", Action = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
        Resource = [
          local.bedrock_model_arn_wildcard,
          local.bedrock_sonnet_inference_profile_arn
        ]
#        Condition = {
#          StringLike = {
#            "aws:InferenceProfileArn" = local.bedrock_sonnet_inference_profile_name
#          }
#        }
      },
      { Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject"], Resource = ["${aws_s3_bucket.uploads.arn}/*"] },
      { Effect = "Allow", Action = ["s3:ListBucket"], Resource = ["${aws_s3_bucket.uploads.arn}"] },
      { Effect = "Allow", Action = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem"], Resource = [aws_dynamodb_table.usage.arn, aws_dynamodb_table.api_keys.arn] },
      { Effect = "Allow", Action = ["textract:DetectDocumentText", "textract:StartDocumentTextDetection", "textract:GetDocumentTextDetection"], Resource = "*" },
      {
        Effect    = "Allow",
        Action    = ["iam:PassRole"],
        Resource  = aws_iam_role.textract_sns_role.arn,
        Condition = { StringEquals = { "iam:PassedToService" = "textract.amazonaws.com" } }
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "lambda_vpc_access" {
  role       = aws_iam_role.lambda_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}

resource "aws_iam_role_policy_attachment" "lambda_app_attach" {
  role       = aws_iam_role.lambda_role.name
  policy_arn = aws_iam_policy.lambda_app_policy.arn
}

# --- Cognito ---
resource "aws_cognito_user_pool" "users" {
  name = "${var.name_prefix}-users"

  # This is potentially dangerous if actual changes need to get made to the schema
  # in which case there would need to be a new pool created with a new schema.
  # Keeping it simple for now :)
  lifecycle {
    ignore_changes = [schema]
  }

  schema {
    name                = "tenant_id"
    attribute_data_type = "String"
    required            = false
    mutable             = false
  }
}
resource "aws_cognito_user_pool_client" "client" {
  name                = "${var.name_prefix}-app-client"
  user_pool_id        = aws_cognito_user_pool.users.id
  generate_secret     = false
  explicit_auth_flows = ["ALLOW_USER_PASSWORD_AUTH", "ALLOW_REFRESH_TOKEN_AUTH", "ALLOW_USER_SRP_AUTH"]
}

# --- HTTP API + JWT authorizer + stage ---
resource "aws_apigatewayv2_api" "api" {
  name          = "${var.name_prefix}-http-api"
  protocol_type = "HTTP"
}
resource "aws_apigatewayv2_authorizer" "jwt" {
  api_id           = aws_apigatewayv2_api.api.id
  name             = "jwt-auth"
  authorizer_type  = "JWT"
  identity_sources = ["$request.header.Authorization"]
  jwt_configuration {
    audience = [aws_cognito_user_pool_client.client.id]
    issuer   = "https://cognito-idp.${var.aws_region}.amazonaws.com/${aws_cognito_user_pool.users.id}"
  }
}
resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.api.id
  name        = "$default"
  auto_deploy = true
  default_route_settings {
    throttling_rate_limit  = 5
    throttling_burst_limit = 10
  }
}

# --- WAF + CloudFront in front of HTTP API ---
resource "aws_wafv2_web_acl" "waf" {
  provider = aws.use1
  name     = "${var.name_prefix}-waf"
  scope    = "CLOUDFRONT"
  default_action {
    allow {
    }
  }
  rule {
    name     = "rate-limit-ip"
    priority = 1
    action {
      block {

      }
    }
    statement {
      rate_based_statement {
        limit              = 1000
        aggregate_key_type = "IP"
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "rate-limit-ip"
      sampled_requests_enabled   = true
    }
  }
  visibility_config {
    cloudwatch_metrics_enabled = true
    metric_name                = "waf"
    sampled_requests_enabled   = true
  }
}

resource "aws_cloudfront_distribution" "cdn" {
  provider = aws.use1
  enabled  = true

  origin {
    domain_name = replace(aws_apigatewayv2_api.api.api_endpoint, "https://", "")
    origin_id   = "http-api-origin"
    custom_origin_config {
      http_port              = 80
      https_port             = 443
      origin_protocol_policy = "https-only"
      origin_ssl_protocols   = ["TLSv1.2"]
    }
  }

  default_cache_behavior {
    target_origin_id       = "http-api-origin"
    viewer_protocol_policy = "https-only"
    allowed_methods        = ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"]
    cached_methods         = ["GET", "HEAD", "OPTIONS"]
    compress               = true
    forwarded_values {
      query_string = true
      headers      = ["Authorization", "Content-Type", "Accept", "x-api-key"]
      cookies { forward = "none" }
    }
    min_ttl     = 0
    default_ttl = 0
    max_ttl     = 0
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }
  viewer_certificate {
    cloudfront_default_certificate = true
  }
  web_acl_id = aws_wafv2_web_acl.waf.arn
}
