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

variable "seed_api_key_value" {
  type = string
}

variable "seed_api_key_tier" {
  type    = string
  default = "pro"
}
