locals {
  mod        = "am05"
  account_id = data.aws_caller_identity.current.account_id
  region     = data.aws_region.current.region
  stack      = "${var.course_prefix}-${var.student_id}-${local.mod}" # shared (per-cohort) resources: Cognito pool, code bucket
  prefix_u   = replace(var.course_prefix, "-", "_")
  domains    = ["inventory", "supplier", "logistics", "quality"]
  student_domains = { for d in local.domains : "${var.student_id}-${d}" => { student = var.student_id, domain = d } }
  discovery_url       = "https://cognito-idp.${local.region}.amazonaws.com/${aws_cognito_user_pool.pool.id}/.well-known/openid-configuration"
  cognito_domain_host = "${aws_cognito_user_pool_domain.domain.domain}.auth.${local.region}.amazoncognito.com"
  common_tags         = { Course = var.course_prefix, Module = local.mod }
}

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

# ---------------------------------------------------------------------------
# Amazon Cognito: ONE user pool per stack, resource server with the two
# supply-chain scopes, and the OAuth 2.0 app client(s).
# ---------------------------------------------------------------------------
resource "aws_cognito_user_pool" "pool" {
  name = "${local.stack}-pool"
  tags = local.common_tags
}

resource "aws_cognito_user_pool_domain" "domain" {
  domain       = "${local.stack}-${substr(local.account_id, 0, 8)}"
  user_pool_id = aws_cognito_user_pool.pool.id
}

resource "aws_cognito_resource_server" "supplychain" {
  identifier   = "supplychain"
  name         = "OrbitOps Supply Chain API"
  user_pool_id = aws_cognito_user_pool.pool.id

  scope {
    scope_name        = "read"
    scope_description = "Read supply chain data"
  }
  scope {
    scope_name        = "write"
    scope_description = "Write supply chain data"
  }
}

# Machine-to-machine app client (client_credentials grant) used by the chat handler and trusted by every JWT authorizer.
resource "aws_cognito_user_pool_client" "m2m" {
  name         = "${var.course_prefix}-${var.student_id}-${local.mod}-m2m"
  user_pool_id = aws_cognito_user_pool.pool.id

  generate_secret                      = true # confidential, server-side client
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_flows                  = ["client_credentials"]
  allowed_oauth_scopes                 = ["supplychain/read", "supplychain/write"]
  supported_identity_providers         = ["COGNITO"]
  access_token_validity                = 60
  token_validity_units {
    access_token = "minutes"
  }

  depends_on = [aws_cognito_resource_server.supplychain]
}

# ---------------------------------------------------------------------------
# Shared S3 bucket: agent code zips (direct code deployment), gateway tool
# schemas, the specialist agent zip and the reference manuals.
# ---------------------------------------------------------------------------
resource "aws_s3_bucket" "code" {
  bucket        = "${local.stack}-${local.account_id}"
  force_destroy = true
  tags          = local.common_tags
}

resource "aws_s3_bucket_public_access_block" "code" {
  bucket                  = aws_s3_bucket.code.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Builds build/orchestrator.zip and build/kb_specialist.zip (boto3 vendored, pure Python)
# at plan time. Needs python3 + pip on the machine running Terraform.
data "external" "build" {
  program = ["python3", "${path.module}/agents/build_agents.py", "${path.module}/agents"]
}

resource "aws_s3_object" "orchestrator_zip" {
  bucket      = aws_s3_bucket.code.id
  key         = "agents/orchestrator.zip"
  source      = data.external.build.result.orchestrator_zip
  source_hash = data.external.build.result.orchestrator_sha256
}

resource "aws_s3_object" "schema" {
  for_each    = toset(local.domains)
  bucket      = aws_s3_bucket.code.id
  key         = "schemas/${each.key}_tools.json"
  source      = "${path.module}/schemas/${each.key}_tools.json"
  source_hash = filemd5("${path.module}/schemas/${each.key}_tools.json")
  content_type = "application/json"
}

resource "aws_s3_object" "kb_specialist_zip" {
  bucket      = aws_s3_bucket.code.id
  key         = "agents/kb_specialist.zip"
  source      = data.external.build.result.kb_specialist_zip
  source_hash = data.external.build.result.kb_specialist_sha256
}

# The supply-chain reference manuals the knowledge base ingests.
resource "aws_s3_object" "docs" {
  for_each     = fileset("${path.module}/docs", "*.md")
  bucket       = aws_s3_bucket.code.id
  key          = "docs/${each.value}"
  source       = "${path.module}/docs/${each.value}"
  source_hash  = filemd5("${path.module}/docs/${each.value}")
  content_type = "text/markdown"
}

# ---------------------------------------------------------------------------
# The four supply-chain tool Lambdas (inventory, supplier, logistics, quality).
# One shared execution role; one function per student per domain.
# ---------------------------------------------------------------------------
data "archive_file" "tool" {
  for_each    = toset(local.domains)
  type        = "zip"
  source_file = "${path.module}/lambdas/${each.key}.py"
  output_path = "${path.module}/build/${each.key}.zip"
}

resource "aws_iam_role" "tools" {
  name = "${local.stack}-tools-role"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = "sts:AssumeRole", Principal = { Service = "lambda.amazonaws.com" } }]
  })
  tags = local.common_tags
}

resource "aws_iam_role_policy_attachment" "tools_basic" {
  role       = aws_iam_role.tools.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_cloudwatch_log_group" "tool" {
  for_each          = local.student_domains
  name              = "/aws/lambda/${var.course_prefix}-${each.value.student}-${local.mod}-${each.value.domain}"
  retention_in_days = var.log_retention_days
  tags              = merge(local.common_tags, { Student = each.value.student })
}

resource "aws_lambda_function" "tool" {
  for_each         = local.student_domains
  function_name    = "${var.course_prefix}-${each.value.student}-${local.mod}-${each.value.domain}"
  role             = aws_iam_role.tools.arn
  handler          = "${each.value.domain}.handler"
  runtime          = "python3.12"
  filename         = data.archive_file.tool[each.value.domain].output_path
  source_code_hash = data.archive_file.tool[each.value.domain].output_base64sha256
  timeout          = 15
  tags             = merge(local.common_tags, { Student = each.value.student, Domain = each.value.domain })
  depends_on       = [aws_cloudwatch_log_group.tool, aws_iam_role_policy_attachment.tools_basic]
}

# ---------------------------------------------------------------------------
# The OrbitOps chat console: a Lambda Function URL that mints a Cognito M2M
# token and invokes the orchestrator runtime with it.
# ---------------------------------------------------------------------------
data "archive_file" "chat" {
  type        = "zip"
  source_file = "${path.module}/lambdas/chat_handler.py"
  output_path = "${path.module}/build/chat_handler.zip"
}

resource "aws_iam_role" "chat" {
  name = "${local.stack}-chat-role"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = "sts:AssumeRole", Principal = { Service = "lambda.amazonaws.com" } }]
  })
  tags = local.common_tags
}

resource "aws_iam_role_policy_attachment" "chat_basic" {
  role       = aws_iam_role.chat.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_cloudwatch_log_group" "chat" {
  name              = "/aws/lambda/${var.course_prefix}-${var.student_id}-${local.mod}-chat-handler"
  retention_in_days = var.log_retention_days
  tags              = merge(local.common_tags, { Student = var.student_id })
}

resource "aws_lambda_function" "chat" {
  function_name    = "${var.course_prefix}-${var.student_id}-${local.mod}-chat-handler"
  role             = aws_iam_role.chat.arn
  handler          = "chat_handler.handler"
  runtime          = "python3.12"
  filename         = data.archive_file.chat.output_path
  source_code_hash = data.archive_file.chat.output_base64sha256
  timeout          = 120
  memory_size      = 256
  tags             = merge(local.common_tags, { Student = var.student_id })

  environment {
    variables = {
      STUDENT_ID = var.student_id
      MODULE     = local.mod
      ORCHESTRATOR_RUNTIME_ARN = aws_bedrockagentcore_agent_runtime.orchestrator.agent_runtime_arn
      COGNITO_DOMAIN           = local.cognito_domain_host
      COGNITO_CLIENT_ID        = aws_cognito_user_pool_client.m2m.id
      COGNITO_CLIENT_SECRET    = aws_cognito_user_pool_client.m2m.client_secret
      COGNITO_USER_POOL_ID     = aws_cognito_user_pool.pool.id
    }
  }

  depends_on = [aws_cloudwatch_log_group.chat, aws_iam_role_policy_attachment.chat_basic]
}

resource "aws_lambda_function_url" "chat" {
  function_name      = aws_lambda_function.chat.function_name
  authorization_type = "NONE"
}

resource "aws_lambda_permission" "chat_url" {
  statement_id           = "AllowPublicFunctionUrl"
  action                 = "lambda:InvokeFunctionUrl"
  function_name          = aws_lambda_function.chat.function_name
  principal              = "*"
  function_url_auth_type = "NONE"
}

# ---------------------------------------------------------------------------
# AgentCore Runtime execution role for the orchestrator.
# ---------------------------------------------------------------------------
resource "aws_iam_role" "runtime" {
  name = "${var.course_prefix}-${var.student_id}-${local.mod}-orchestrator-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = "bedrock-agentcore.amazonaws.com" }
      Condition = { StringEquals = { "aws:SourceAccount" = local.account_id } }
    }]
  })
  tags = merge(local.common_tags, { Student = var.student_id })
}

resource "aws_iam_role_policy" "runtime" {
  name = "orchestrator-permissions"
  role = aws_iam_role.runtime.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "Logs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogStreams", "logs:DescribeLogGroups", "logs:PutResourcePolicy"]
        Resource = "arn:aws:logs:${local.region}:${local.account_id}:log-group:*"
      },
      {
        Sid      = "Telemetry"
        Effect   = "Allow"
        Action   = ["xray:PutTraceSegments", "xray:PutTelemetryRecords", "xray:GetSamplingRules", "xray:GetSamplingTargets", "cloudwatch:PutMetricData"]
        Resource = "*"
      },
      {
        Sid      = "DeploymentPackage"
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:GetObjectVersion"]
        Resource = "${aws_s3_bucket.code.arn}/agents/*"
      },
      {
        Sid      = "Models"
        Effect   = "Allow"
        Action   = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
        Resource = ["arn:aws:bedrock:*::foundation-model/*", "arn:aws:bedrock:*:${local.account_id}:inference-profile/*"]
      },
      {
        Sid      = "Guardrails"
        Effect   = "Allow"
        Action   = ["bedrock:ApplyGuardrail"]
        Resource = "arn:aws:bedrock:${local.region}:${local.account_id}:guardrail/*"
      },
      {
        Sid      = "MemoryDataPlane"
        Effect   = "Allow"
        Action   = ["bedrock-agentcore:CreateEvent", "bedrock-agentcore:GetEvent", "bedrock-agentcore:ListEvents", "bedrock-agentcore:RetrieveMemoryRecords", "bedrock-agentcore:ListMemoryRecords", "bedrock-agentcore:GetMemoryRecord", "bedrock-agentcore:ListSessions", "bedrock-agentcore:ListActors"]
        Resource = "arn:aws:bedrock-agentcore:${local.region}:${local.account_id}:memory/*"
      },
      {
        Sid      = "AgentToAgent"
        Effect   = "Allow"
        Action   = ["bedrock-agentcore:InvokeAgentRuntime"]
        Resource = "arn:aws:bedrock-agentcore:${local.region}:${local.account_id}:runtime/*"
      },
      {
        Sid      = "WorkloadIdentity"
        Effect   = "Allow"
        Action   = ["bedrock-agentcore:GetWorkloadAccessToken", "bedrock-agentcore:GetWorkloadAccessTokenForJWT", "bedrock-agentcore:GetWorkloadAccessTokenForUserId"]
        Resource = ["arn:aws:bedrock-agentcore:${local.region}:${local.account_id}:workload-identity-directory/default", "arn:aws:bedrock-agentcore:${local.region}:${local.account_id}:workload-identity-directory/default/workload-identity/*"]
      }
    ]
  })
}

# IAM is eventually consistent; give new roles a moment before AgentCore validates them.
resource "time_sleep" "iam_propagation" {
  create_duration = "15s"
  depends_on      = [aws_iam_role_policy.runtime, aws_iam_role_policy.specialist, aws_iam_role_policy.kb, aws_iam_role_policy.gateway]
}

# ---------------------------------------------------------------------------
# AgentCore Gateway: the MCP front door to the supply-chain tools.
# JWT authorizer trusts the Cognito app client; targets wrap the Lambdas.
# ---------------------------------------------------------------------------
resource "aws_iam_role" "gateway" {
  name = "${var.course_prefix}-${var.student_id}-${local.mod}-gateway-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = "bedrock-agentcore.amazonaws.com" }
      Condition = { StringEquals = { "aws:SourceAccount" = local.account_id } }
    }]
  })
  tags = merge(local.common_tags, { Student = var.student_id })
}

resource "aws_iam_role_policy" "gateway" {
  name = "invoke-tools"
  role = aws_iam_role.gateway.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "InvokeToolLambdas"
        Effect   = "Allow"
        Action   = ["lambda:InvokeFunction"]
        Resource = [for d in local.domains : aws_lambda_function.tool["${var.student_id}-${d}"].arn]
      },
      {
        Sid      = "ReadToolSchemas"
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = "${aws_s3_bucket.code.arn}/schemas/*"
      }
    ]
  })
}

resource "aws_bedrockagentcore_gateway" "gw" {
  name        = "${var.course_prefix}-${var.student_id}-${local.mod}-gateway"
  description = "OrbitOps supply-chain tool gateway (${var.student_id})"
  role_arn    = aws_iam_role.gateway.arn

  authorizer_type = "CUSTOM_JWT"
  authorizer_configuration {
    custom_jwt_authorizer {
      discovery_url = local.discovery_url
      allowed_clients = [aws_cognito_user_pool_client.m2m.id]
    }
  }

  protocol_type = "MCP"
  protocol_configuration {
    mcp {
      instructions = "Tools for OrbitOps cargo inventory, suppliers, logistics and quality inspections."
    }
  }

  tags       = merge(local.common_tags, { Student = var.student_id })
  depends_on = [aws_iam_role_policy.gateway, time_sleep.iam_propagation]
}

resource "aws_bedrockagentcore_gateway_target" "inventory" {
  name               = "inventory-target"
  gateway_identifier = aws_bedrockagentcore_gateway.gw.gateway_id
  description        = "Cargo stock levels"

  credential_provider_configuration {
    gateway_iam_role {}
  }
  target_configuration {
    mcp {
      lambda {
        lambda_arn = aws_lambda_function.tool["${var.student_id}-inventory"].arn
        tool_schema {
          s3 {
            uri = "s3://${aws_s3_bucket.code.id}/${aws_s3_object.schema["inventory"].key}"
          }
        }
      }
    }
  }
}

resource "aws_bedrockagentcore_gateway_target" "supplier" {
  name               = "supplier-target"
  gateway_identifier = aws_bedrockagentcore_gateway.gw.gateway_id
  description        = "Supplier records"

  credential_provider_configuration {
    gateway_iam_role {}
  }
  target_configuration {
    mcp {
      lambda {
        lambda_arn = aws_lambda_function.tool["${var.student_id}-supplier"].arn
        tool_schema {
          s3 {
            uri = "s3://${aws_s3_bucket.code.id}/${aws_s3_object.schema["supplier"].key}"
          }
        }
      }
    }
  }
  depends_on = [aws_bedrockagentcore_gateway_target.inventory]
}

resource "aws_bedrockagentcore_gateway_target" "logistics" {
  name               = "logistics-target"
  gateway_identifier = aws_bedrockagentcore_gateway.gw.gateway_id
  description        = "Shipment tracking"

  credential_provider_configuration {
    gateway_iam_role {}
  }
  target_configuration {
    mcp {
      lambda {
        lambda_arn = aws_lambda_function.tool["${var.student_id}-logistics"].arn
        tool_schema {
          s3 {
            uri = "s3://${aws_s3_bucket.code.id}/${aws_s3_object.schema["logistics"].key}"
          }
        }
      }
    }
  }
  depends_on = [aws_bedrockagentcore_gateway_target.supplier]
}

resource "aws_bedrockagentcore_gateway_target" "quality" {
  name               = "quality-target"
  gateway_identifier = aws_bedrockagentcore_gateway.gw.gateway_id
  description        = "Quality inspections"

  credential_provider_configuration {
    gateway_iam_role {}
  }
  target_configuration {
    mcp {
      lambda {
        lambda_arn = aws_lambda_function.tool["${var.student_id}-quality"].arn
        tool_schema {
          s3 {
            uri = "s3://${aws_s3_bucket.code.id}/${aws_s3_object.schema["quality"].key}"
          }
        }
      }
    }
  }
  depends_on = [aws_bedrockagentcore_gateway_target.logistics]
}

# ---------------------------------------------------------------------------
# Knowledge-base plumbing that is ALWAYS provided: vector store (S3 Vectors,
# the low-cost stand-in for the micro-credential's OpenSearch Serverless
# collection), the KB service role, the specialist runtime role, and the
# specialist's code package.
# ---------------------------------------------------------------------------
resource "aws_s3vectors_vector_bucket" "kb" {
  vector_bucket_name = "${var.course_prefix}-${var.student_id}-${local.mod}-vectors"
  force_destroy      = true
  tags               = merge(local.common_tags, { Student = var.student_id })
}

resource "aws_s3vectors_index" "kb" {
  vector_bucket_name = aws_s3vectors_vector_bucket.kb.vector_bucket_name
  index_name         = "bedrock-knowledge-base-index"
  data_type          = "float32"
  dimension          = var.embedding_dimensions
  distance_metric    = "cosine"
  metadata_configuration {
    non_filterable_metadata_keys = ["AMAZON_BEDROCK_TEXT", "AMAZON_BEDROCK_METADATA"]
  }
  tags = merge(local.common_tags, { Student = var.student_id })
}

resource "aws_iam_role" "kb" {
  name = "${var.course_prefix}-${var.student_id}-${local.mod}-kb-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = "bedrock.amazonaws.com" }
      Condition = { StringEquals = { "aws:SourceAccount" = local.account_id } }
    }]
  })
  tags = merge(local.common_tags, { Student = var.student_id })
}

resource "aws_iam_role_policy" "kb" {
  name = "knowledge-base-permissions"
  role = aws_iam_role.kb.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "EmbeddingModel"
        Effect   = "Allow"
        Action   = ["bedrock:InvokeModel"]
        Resource = "arn:aws:bedrock:${local.region}::foundation-model/${var.embedding_model_id}"
      },
      {
        Sid      = "ReadManuals"
        Effect   = "Allow"
        Action   = ["s3:ListBucket"]
        Resource = aws_s3_bucket.code.arn
      },
      {
        Sid      = "ReadManualObjects"
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = "${aws_s3_bucket.code.arn}/docs/*"
      },
      {
        Sid      = "VectorStore"
        Effect   = "Allow"
        Action   = ["s3vectors:PutVectors", "s3vectors:GetVectors", "s3vectors:DeleteVectors", "s3vectors:QueryVectors", "s3vectors:ListVectors", "s3vectors:GetIndex", "s3vectors:GetVectorBucket"]
        Resource = [aws_s3vectors_vector_bucket.kb.vector_bucket_arn, aws_s3vectors_index.kb.index_arn]
      }
    ]
  })
}

resource "aws_iam_role" "specialist" {
  name = "${var.course_prefix}-${var.student_id}-${local.mod}-kb-specialist-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = "bedrock-agentcore.amazonaws.com" }
      Condition = { StringEquals = { "aws:SourceAccount" = local.account_id } }
    }]
  })
  tags = merge(local.common_tags, { Student = var.student_id })
}

resource "aws_iam_role_policy" "specialist" {
  name = "specialist-permissions"
  role = aws_iam_role.specialist.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "Logs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogStreams", "logs:DescribeLogGroups", "logs:PutResourcePolicy"]
        Resource = "arn:aws:logs:${local.region}:${local.account_id}:log-group:*"
      },
      {
        Sid      = "Telemetry"
        Effect   = "Allow"
        Action   = ["xray:PutTraceSegments", "xray:PutTelemetryRecords", "xray:GetSamplingRules", "xray:GetSamplingTargets", "cloudwatch:PutMetricData"]
        Resource = "*"
      },
      {
        Sid      = "DeploymentPackage"
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:GetObjectVersion"]
        Resource = "${aws_s3_bucket.code.arn}/agents/*"
      },
      {
        Sid      = "Models"
        Effect   = "Allow"
        Action   = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
        Resource = ["arn:aws:bedrock:*::foundation-model/*", "arn:aws:bedrock:*:${local.account_id}:inference-profile/*"]
      },
      {
        Sid      = "KnowledgeBaseRetrieval"
        Effect   = "Allow"
        Action   = ["bedrock:Retrieve", "bedrock:RetrieveAndGenerate"]
        Resource = "arn:aws:bedrock:${local.region}:${local.account_id}:knowledge-base/*"
      },
      {
        Sid      = "WorkloadIdentity"
        Effect   = "Allow"
        Action   = ["bedrock-agentcore:GetWorkloadAccessToken", "bedrock-agentcore:GetWorkloadAccessTokenForJWT", "bedrock-agentcore:GetWorkloadAccessTokenForUserId"]
        Resource = ["arn:aws:bedrock-agentcore:${local.region}:${local.account_id}:workload-identity-directory/default", "arn:aws:bedrock-agentcore:${local.region}:${local.account_id}:workload-identity-directory/default/workload-identity/*"]
      }
    ]
  })
}

# ---------------------------------------------------------------------------
# The knowledge base, its S3 data source (synced) and the specialist runtime. In Mission E these already work; the lab is about their network path.
# ---------------------------------------------------------------------------
resource "aws_bedrockagent_knowledge_base" "kb" {
  name        = "${var.course_prefix}-${var.student_id}-${local.mod}-kb"
  description = "OrbitOps supply-chain manuals (${var.student_id})"
  role_arn    = aws_iam_role.kb.arn

  knowledge_base_configuration {
    type = "VECTOR"
    vector_knowledge_base_configuration {
      embedding_model_arn = "arn:aws:bedrock:${local.region}::foundation-model/${var.embedding_model_id}"
      embedding_model_configuration {
        bedrock_embedding_model_configuration {
          dimensions          = var.embedding_dimensions
          embedding_data_type = "FLOAT32"
        }
      }
    }
  }

  storage_configuration {
    type = "S3_VECTORS"
    s3_vectors_configuration {
      index_arn = aws_s3vectors_index.kb.index_arn
    }
  }

  tags       = merge(local.common_tags, { Student = var.student_id })
  depends_on = [aws_iam_role_policy.kb, time_sleep.iam_propagation]
}

resource "aws_bedrockagent_data_source" "manuals" {
  knowledge_base_id    = aws_bedrockagent_knowledge_base.kb.id
  name                 = "supply-chain-manuals"
  data_deletion_policy = "RETAIN"

  data_source_configuration {
    type = "S3"
    s3_configuration {
      bucket_arn         = aws_s3_bucket.code.arn
      inclusion_prefixes = ["docs/"]
    }
  }

  vector_ingestion_configuration {
    chunking_configuration {
      chunking_strategy = "FIXED_SIZE"
      fixed_size_chunking_configuration {
        max_tokens         = 300
        overlap_percentage = 20
      }
    }
  }

  depends_on = [aws_s3_object.docs]
}

# Sync the data source (ingest the manuals) and wait for it to complete.
resource "terraform_data" "kb_sync" {
  triggers_replace = [aws_bedrockagent_data_source.manuals.data_source_id, data.external.build.result.kb_specialist_sha256, join(",", [for o in aws_s3_object.docs : o.etag])]

  provisioner "local-exec" {
    command = "python3 ${path.module}/agents/start_ingestion.py ${aws_bedrockagent_knowledge_base.kb.id} ${aws_bedrockagent_data_source.manuals.data_source_id} ${local.region}"
  }
}

resource "aws_bedrockagentcore_agent_runtime" "specialist" {
  agent_runtime_name = "${local.prefix_u}_${var.student_id}_${local.mod}_kb_specialist"
  description        = "OrbitOps knowledge-base specialist (${var.student_id})"
  role_arn           = aws_iam_role.specialist.arn

  agent_runtime_artifact {
    code_configuration {
      entry_point = ["main.py"]
      runtime     = "PYTHON_3_12"
      code {
        s3 {
          bucket = aws_s3_bucket.code.id
          prefix = aws_s3_object.kb_specialist_zip.key
        }
      }
    }
  }

  protocol_configuration {
    server_protocol = "HTTP"
  }

  authorizer_configuration {
    custom_jwt_authorizer {
      discovery_url   = local.discovery_url
      allowed_clients = [aws_cognito_user_pool_client.m2m.id]
    }
  }

  network_configuration {
    network_mode = "PUBLIC"
  }

  environment_variables = {
    MODEL_ID          = var.model_id
    ORBIT_REGION      = local.region
    KNOWLEDGE_BASE_ID = aws_bedrockagent_knowledge_base.kb.id
    CODE_SHA256       = substr(data.external.build.result.kb_specialist_sha256, 0, 12) # new zip => new runtime version
  }

  tags       = merge(local.common_tags, { Student = var.student_id })
  depends_on = [aws_s3_object.kb_specialist_zip, time_sleep.iam_propagation]
}

# ---------------------------------------------------------------------------
# The prepared VPC (one per student): public + private subnets in two AZs, an
# internet gateway, an S3 gateway endpoint, a security group, and an optional
# NAT gateway. The runtimes must be moved INTO it (Mission E).
# ---------------------------------------------------------------------------
locals {
  azs = ["${local.region}a", "${local.region}b"]
}

resource "aws_vpc" "lab" {
  cidr_block           = cidrsubnet("10.0.0.0/8", 8, 0)
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = merge(local.common_tags, { Student = var.student_id, Name = "${var.course_prefix}-${var.student_id}-${local.mod}-vpc" })
}

resource "aws_internet_gateway" "igw" {
  vpc_id = aws_vpc.lab.id
  tags   = merge(local.common_tags, { Student = var.student_id, Name = "${var.course_prefix}-${var.student_id}-${local.mod}-igw" })
}

resource "aws_subnet" "public_a" {
  vpc_id                  = aws_vpc.lab.id
  cidr_block              = cidrsubnet(aws_vpc.lab.cidr_block, 8, 0)
  availability_zone       = local.azs[0]
  map_public_ip_on_launch = true
  tags                    = merge(local.common_tags, { Student = var.student_id, Name = "${var.course_prefix}-${var.student_id}-${local.mod}-public-a", Tier = "public" })
}

resource "aws_subnet" "public_b" {
  vpc_id                  = aws_vpc.lab.id
  cidr_block              = cidrsubnet(aws_vpc.lab.cidr_block, 8, 1)
  availability_zone       = local.azs[1]
  map_public_ip_on_launch = true
  tags                    = merge(local.common_tags, { Student = var.student_id, Name = "${var.course_prefix}-${var.student_id}-${local.mod}-public-b", Tier = "public" })
}

resource "aws_subnet" "private_a" {
  vpc_id            = aws_vpc.lab.id
  cidr_block        = cidrsubnet(aws_vpc.lab.cidr_block, 8, 10)
  availability_zone = local.azs[0]
  tags              = merge(local.common_tags, { Student = var.student_id, Name = "${var.course_prefix}-${var.student_id}-${local.mod}-private-a", Tier = "private" })
}

resource "aws_subnet" "private_b" {
  vpc_id            = aws_vpc.lab.id
  cidr_block        = cidrsubnet(aws_vpc.lab.cidr_block, 8, 11)
  availability_zone = local.azs[1]
  tags              = merge(local.common_tags, { Student = var.student_id, Name = "${var.course_prefix}-${var.student_id}-${local.mod}-private-b", Tier = "private" })
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.lab.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.igw.id
  }
  tags = merge(local.common_tags, { Student = var.student_id, Name = "${var.course_prefix}-${var.student_id}-${local.mod}-public-rt" })
}

resource "aws_route_table_association" "public_a" {
  subnet_id      = aws_subnet.public_a.id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table_association" "public_b" {
  subnet_id      = aws_subnet.public_b.id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table" "private" {
  vpc_id = aws_vpc.lab.id
  tags   = merge(local.common_tags, { Student = var.student_id, Name = "${var.course_prefix}-${var.student_id}-${local.mod}-private-rt" })
}

resource "aws_route_table_association" "private_a" {
  subnet_id      = aws_subnet.private_a.id
  route_table_id = aws_route_table.private.id
}

resource "aws_route_table_association" "private_b" {
  subnet_id      = aws_subnet.private_b.id
  route_table_id = aws_route_table.private.id
}

# Optional NAT gateway (off by default; see variables.tf).
resource "aws_eip" "nat" {
  for_each = var.enable_nat_gateway ? toset([var.student_id]) : toset([])
  domain   = "vpc"
  tags     = merge(local.common_tags, { Student = each.key, Name = "${var.course_prefix}-${each.key}-${local.mod}-nat-eip" })
}

resource "aws_nat_gateway" "nat" {
  for_each      = var.enable_nat_gateway ? toset([var.student_id]) : toset([])
  allocation_id = aws_eip.nat[each.key].id
  subnet_id     = aws_subnet.public_a.id
  tags          = merge(local.common_tags, { Student = each.key, Name = "${var.course_prefix}-${each.key}-${local.mod}-nat" })
  depends_on    = [aws_internet_gateway.igw]
}

resource "aws_route" "private_default" {
  for_each               = var.enable_nat_gateway ? toset([var.student_id]) : toset([])
  route_table_id         = aws_route_table.private.id
  destination_cidr_block = "0.0.0.0/0"
  nat_gateway_id         = aws_nat_gateway.nat[each.key].id
}

# Free gateway endpoint so private subnets can reach S3 without the internet.
resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.lab.id
  service_name      = "com.amazonaws.${local.region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.private.id]
  tags              = merge(local.common_tags, { Student = var.student_id, Name = "${var.course_prefix}-${var.student_id}-${local.mod}-s3-endpoint" })
}

resource "aws_security_group" "agents" {
  name        = "${var.course_prefix}-${var.student_id}-${local.mod}-agents-sg"
  description = "OrbitOps agent runtimes and interface endpoints"
  vpc_id      = aws_vpc.lab.id

  ingress {
    description = "HTTPS from inside the VPC"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = [aws_vpc.lab.cidr_block]
  }
  egress {
    description = "all outbound"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
  tags = merge(local.common_tags, { Student = var.student_id, Name = "${var.course_prefix}-${var.student_id}-${local.mod}-agents-sg" })
}

# ---------------------------------------------------------------------------
# The orchestrator agent (AgentCore Runtime, direct code deployment).
# ---------------------------------------------------------------------------
resource "aws_bedrockagentcore_agent_runtime" "orchestrator" {
  agent_runtime_name = "${local.prefix_u}_${var.student_id}_${local.mod}_orchestrator"
  description        = "OrbitOps flight-controller orchestrator (${var.student_id})"
  role_arn           = aws_iam_role.runtime.arn

  agent_runtime_artifact {
    code_configuration {
      entry_point = ["main.py"]
      runtime     = "PYTHON_3_12"
      code {
        s3 {
          bucket = aws_s3_bucket.code.id
          prefix = aws_s3_object.orchestrator_zip.key
        }
      }
    }
  }

  protocol_configuration {
    server_protocol = "HTTP"
  }

  # Inbound auth: only Cognito tokens from the allowed app client are accepted.
  authorizer_configuration {
    custom_jwt_authorizer {
      discovery_url = local.discovery_url
      allowed_clients = [aws_cognito_user_pool_client.m2m.id]
    }
  }

  # Forward the caller's bearer token to the agent so it can call the Gateway
  # and the specialist with the same identity (token pass-through).
  request_header_configuration {
    request_header_allowlist = ["Authorization"]
  }

  network_configuration {
    network_mode = "PUBLIC"
  }

  environment_variables = {
    MODEL_ID          = var.model_id
    ORBIT_REGION      = local.region
    STUDENT_ID        = var.student_id
    GUARDRAIL_VERSION = "DRAFT"
    # The runtime copies its code from S3 only when a new version is created, so a code change
    # must change the configuration: the source hash forces a new version whenever the zip changes.
    CODE_SHA256 = substr(data.external.build.result.orchestrator_sha256, 0, 12)
    GATEWAY_URL       = aws_bedrockagentcore_gateway.gw.gateway_url
    KB_SPECIALIST_RUNTIME_ARN = aws_bedrockagentcore_agent_runtime.specialist.agent_runtime_arn
  }

  tags       = merge(local.common_tags, { Student = var.student_id })
  depends_on = [aws_s3_object.orchestrator_zip, time_sleep.iam_propagation]
}
