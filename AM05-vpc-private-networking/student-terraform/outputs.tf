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

output "gateway_urls" {
  description = "AgentCore Gateway MCP endpoint per student (value for GATEWAY_URL)."
  value       = aws_bedrockagentcore_gateway.gw.gateway_url
}

output "gateway_ids" {
  description = "AgentCore Gateway id per student."
  value       = aws_bedrockagentcore_gateway.gw.gateway_id
}

output "solution_resources_kb" {
  description = "Mission C 'Solution resources': everything a student needs to build the knowledge base and specialist."
  value       = {
    KnowledgeBaseBucketName    = aws_s3_bucket.code.id
    KnowledgeBaseDocsPrefix    = "docs/"
    VectorBucketName           = aws_s3vectors_vector_bucket.kb.vector_bucket_name
    VectorBucketArn            = aws_s3vectors_vector_bucket.kb.vector_bucket_arn
    VectorIndexName            = aws_s3vectors_index.kb.index_name
    VectorIndexArn             = aws_s3vectors_index.kb.index_arn
    EmbeddingModelId           = var.embedding_model_id
    EmbeddingDimensions        = tostring(var.embedding_dimensions)
    KnowledgeBaseRoleArn       = aws_iam_role.kb.arn
    KBSpecialistRoleArn        = aws_iam_role.specialist.arn
    KBSpecialistAgentCodeS3Uri = "s3://${aws_s3_bucket.code.id}/${aws_s3_object.kb_specialist_zip.key}"
    KBSpecialistModelId        = var.model_id
  }
}

output "knowledge_base_ids" {
  description = "Bedrock knowledge base id per student."
  value       = aws_bedrockagent_knowledge_base.kb.id
}

output "kb_specialist_runtime_arns" {
  description = "Knowledge-base specialist runtime ARN per student."
  value       = aws_bedrockagentcore_agent_runtime.specialist.agent_runtime_arn
}

output "solution_resources_vpc" {
  description = "Mission E 'Solution resources': the prepared VPC per student."
  value       = {
    VpcId            = aws_vpc.lab.id
    VpcCidr          = aws_vpc.lab.cidr_block
    PrivateSubnetIds = [aws_subnet.private_a.id, aws_subnet.private_b.id]
    PublicSubnetIds  = [aws_subnet.public_a.id, aws_subnet.public_b.id]
    PrivateRouteTableId = aws_route_table.private.id
    SecurityGroupId  = aws_security_group.agents.id
  }
}
