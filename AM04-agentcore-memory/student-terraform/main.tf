locals {
  mod        = "am04"
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
# schemas.
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
  depends_on      = [aws_iam_role_policy.runtime, aws_iam_role_policy_attachment.memory_models]
}

# ---------------------------------------------------------------------------
# AgentCore Memory execution role (provided; the memory resource itself is
# what Mission D is about).
# ---------------------------------------------------------------------------
resource "aws_iam_role" "memory" {
  name = "${var.course_prefix}-${var.student_id}-${local.mod}-memory-role"
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

resource "aws_iam_role_policy_attachment" "memory_models" {
  role       = aws_iam_role.memory.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonBedrockAgentCoreMemoryBedrockModelInferenceExecutionRolePolicy"
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
    MEMORY_ID         = ""
  }

  tags       = merge(local.common_tags, { Student = var.student_id })
  depends_on = [aws_s3_object.orchestrator_zip, time_sleep.iam_propagation]
}
