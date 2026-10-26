variable "region" {
  type        = string
  description = "AWS region. Amazon Bedrock AgentCore and the Nova models must be available here."
  default     = "us-west-2"
}

variable "course_prefix" {
  type        = string
  description = "Short run identifier used in every resource name, e.g. 'orbit'. Use a different value for the solution stack (e.g. 'orbit-sol')."
  default     = "orbit"
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,9}$", var.course_prefix))
    error_message = "course_prefix: 2-10 chars, lowercase letters/digits/hyphens, starting with a letter."
  }
}

variable "student_id" {
  type        = string
  description = "Your unique identifier (lowercase letters and digits only, e.g. 'jsmith42'). Prefixes every resource this stack creates so it never collides with a classmate's resources in a shared account. No default on purpose."
  validation {
    condition     = can(regex("^[a-z0-9]{3,12}$", var.student_id))
    error_message = "student_id must be 3-12 lowercase letters or digits (no hyphens), e.g. \"jsmith42\"."
  }
}

variable "log_retention_days" {
  type        = number
  description = "CloudWatch Logs retention for the Lambda functions (days)."
  default     = 7
  validation {
    condition     = contains([1, 3, 5, 7, 14, 30], var.log_retention_days)
    error_message = "log_retention_days must be one of 1, 3, 5, 7, 14, 30."
  }
}

variable "model_id" {
  type        = string
  description = "Bedrock model (or cross-region inference profile) used by the agents. Nova 2 Lite is cheap and supports tool use + guardrails."
  default     = "us.amazon.nova-2-lite-v1:0"
}
