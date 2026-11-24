variable "aws_region" {
  type    = string
  default = "eu-west-1"
}

variable "name_prefix" {
  type    = string
  default = "ai"
}

variable "vpc_id" {
  type = string
}

variable "private_subnet_ids" {
  type = list(string)
}

variable "private_route_table_ids" {
  type = list(string)
}

variable "cors_allowed_origins" {
  type    = list(string)
  default = ["*"]
}

variable "s3_console_reader_principal_arns" {
  type        = list(string)
  description = "IAM principal ARNs allowed to browse and read the uploads bucket from the AWS console."
  default     = ["arn:aws:iam::521706538227:user/Administrator"]
}

variable "seed_api_key_value" {
  type = string
}

variable "seed_api_key_tier" {
  type    = string
  default = "pro"
}
