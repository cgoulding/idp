output "aws_region" { value = var.aws_region }
output "name_prefix" { value = var.name_prefix }

output "upload_bucket" { value = aws_s3_bucket.uploads.bucket }
output "usage_table" { value = aws_dynamodb_table.usage.name }
output "api_keys_table" { value = aws_dynamodb_table.api_keys.name }

output "cognito_user_pool_id" { value = aws_cognito_user_pool.users.id }
output "cognito_app_client_id" { value = aws_cognito_user_pool_client.client.id }

output "http_api_id" { value = aws_apigatewayv2_api.api.id }
output "http_api_endpoint" { value = aws_apigatewayv2_api.api.api_endpoint }
output "http_api_execution_arn" { value = aws_apigatewayv2_api.api.execution_arn }
output "http_api_stage_name" { value = aws_apigatewayv2_stage.default.name }
output "jwt_authorizer_id" { value = aws_apigatewayv2_authorizer.jwt.id }

output "cloudfront_url" { value = "https://${aws_cloudfront_distribution.cdn.domain_name}" }

output "lambda_role_arn" { value = aws_iam_role.lambda_role.arn }
output "lambda_sg_id" { value = aws_security_group.lambda_sg.id }

output "textract_sns_topic_arn" { value = aws_sns_topic.textract_notifications.arn }
output "textract_role_arn" { value = aws_iam_role.textract_sns_role.arn }

output "textract_results_prefix" { value = "textract-results/" }
output "private_subnet_ids" { value = var.private_subnet_ids }