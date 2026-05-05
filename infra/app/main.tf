terraform {
  required_version = ">= 1.6.0"
  required_providers {
    aws     = {
      source = "hashicorp/aws",
      version = ">= 5.40"
    }
    archive = {
      source = "hashicorp/archive",
      version = ">= 2.4"
    }
  }
}

data "terraform_remote_state" "platform" {
  backend = "local"
  config = {
    path = var.platform_state_path
  }
}

provider "aws" {
  region = data.terraform_remote_state.platform.outputs.aws_region
}

# Package lambda from shared infra/lambda directory
data "archive_file" "lambda_zip" {
  type        = "zip"
  source_dir  = "${path.module}/../lambda"
  output_path = "${path.module}/lambda.zip"
}

resource "aws_lambda_function" "processor" {
  function_name = "${data.terraform_remote_state.platform.outputs.name_prefix}-doc-processor"
  role          = data.terraform_remote_state.platform.outputs.lambda_role_arn

  runtime = "python3.12"
  handler = "handler.lambda_handler"

  filename         = data.archive_file.lambda_zip.output_path
  source_code_hash = data.archive_file.lambda_zip.output_base64sha256

  timeout     = 180
  memory_size = 2048
  vpc_config {
    subnet_ids         = data.terraform_remote_state.platform.outputs.private_subnet_ids
    security_group_ids = [data.terraform_remote_state.platform.outputs.lambda_sg_id]
  }

  environment {
    variables = {
      UPLOAD_BUCKET           = data.terraform_remote_state.platform.outputs.upload_bucket
      BEDROCK_MODEL_ID        = var.bedrock_model_id
      USAGE_TABLE             = data.terraform_remote_state.platform.outputs.usage_table
      API_KEYS_TABLE          = data.terraform_remote_state.platform.outputs.api_keys_table
      MONTHLY_TOKEN_FREE      = tostring(var.monthly_token_limit_free)
      MONTHLY_TOKEN_PRO       = tostring(var.monthly_token_limit_pro)
      TEXTRACT_SNS_TOPIC_ARN  = data.terraform_remote_state.platform.outputs.textract_sns_topic_arn
      TEXTRACT_ROLE_ARN       = data.terraform_remote_state.platform.outputs.textract_role_arn
      TEXTRACT_RESULTS_PREFIX = data.terraform_remote_state.platform.outputs.textract_results_prefix
    }
  }
}

# API Gateway integration + routes
resource "aws_apigatewayv2_integration" "lambda" {
  api_id                 = data.terraform_remote_state.platform.outputs.http_api_id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.processor.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "upload_url" {
  api_id    = data.terraform_remote_state.platform.outputs.http_api_id
  route_key = "POST /upload-url"
  target    = "integrations/${aws_apigatewayv2_integration.lambda.id}"

  authorization_type = "JWT"
  authorizer_id      = data.terraform_remote_state.platform.outputs.jwt_authorizer_id
}

resource "aws_apigatewayv2_route" "invoke" {
  api_id    = data.terraform_remote_state.platform.outputs.http_api_id
  route_key = "POST /invoke"
  target    = "integrations/${aws_apigatewayv2_integration.lambda.id}"

  authorization_type = "JWT"
  authorizer_id      = data.terraform_remote_state.platform.outputs.jwt_authorizer_id
}

resource "aws_apigatewayv2_route" "status" {
  api_id    = data.terraform_remote_state.platform.outputs.http_api_id
  route_key = "POST /status"
  target    = "integrations/${aws_apigatewayv2_integration.lambda.id}"

  authorization_type = "JWT"
  authorizer_id      = data.terraform_remote_state.platform.outputs.jwt_authorizer_id
}

resource "aws_lambda_permission" "allow_apigw" {
  statement_id  = "AllowHttpApiInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.processor.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${data.terraform_remote_state.platform.outputs.http_api_execution_arn}/*/*"
}
