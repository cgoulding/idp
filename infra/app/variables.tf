variable "platform_state_path" {
  type        = string
  description = "Path to the platform terraform.tfstate (local state)."
}

variable "bedrock_model_id" {
  type    = string
  default = "anthropic.claude-3-5-sonnet-20240620-v1:0"
}

variable "monthly_token_limit_free" {
  type = number
  default = 50000
}
variable "monthly_token_limit_pro"  {
  type = number
  default = 1000000
}
