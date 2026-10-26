# Hand students the values for THEIR student_id.

output "chat_urls" {
  description = "OrbitOps chat console URL per student (open in a browser; GET /health shows the config state)."
  value       = aws_lambda_function_url.chat.function_url
}

output "chat_function_names" {
  description = "Chat handler Lambda name per student."
  value       = aws_lambda_function.chat.function_name
}

output "orchestrator_runtime_arns" {
  description = "Orchestrator AgentCore Runtime ARN per student."
  value       = aws_bedrockagentcore_agent_runtime.orchestrator.agent_runtime_arn
}

output "orchestrator_runtime_ids" {
  description = "Orchestrator AgentCore Runtime id per student."
  value       = aws_bedrockagentcore_agent_runtime.orchestrator.agent_runtime_id
}

output "cognito_user_pool_id" {
  description = "Shared Cognito user pool id."
  value       = aws_cognito_user_pool.pool.id
}

output "cognito_domain" {
  description = "Cognito hosted domain (token endpoint is https://<domain>/oauth2/token)."
  value       = local.cognito_domain_host
}

output "cognito_discovery_url" {
  description = "OIDC discovery URL used by every JWT authorizer."
  value       = local.discovery_url
}

output "m2m_client_ids" {
  description = "Machine-to-machine app client id per student."
  value       = aws_cognito_user_pool_client.m2m.id
}

output "m2m_client_secrets" {
  description = "Machine-to-machine app client secret per student (sensitive)."
  value       = aws_cognito_user_pool_client.m2m.client_secret
  sensitive   = true
}

output "tool_function_arns" {
  description = "Tool Lambda ARN per student and domain."
  value       = { for k, v in aws_lambda_function.tool : k => v.arn }
}

output "code_bucket" {
  description = "Shared S3 bucket holding agent zips, tool schemas and manuals."
  value       = aws_s3_bucket.code.id
}

output "quality_schema_s3_uri" {
  description = "S3 URI of the quality tool schema (used when creating quality-target)."
  value       = "s3://${aws_s3_bucket.code.id}/${aws_s3_object.schema["quality"].key}"
}

output "memory_execution_role_arns" {
  description = "Mission D 'Solution resources': memory execution role ARN per student."
  value       = aws_iam_role.memory.arn
}
