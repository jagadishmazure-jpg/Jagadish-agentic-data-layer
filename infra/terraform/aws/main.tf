data "aws_caller_identity" "current" {}

# ---------------------------------------------------------------- encryption
resource "aws_kms_key" "lake" {
  description             = "Agentic data layer lake (${local.suffix})"
  enable_key_rotation     = true
  deletion_window_in_days = 7
  policy                  = data.aws_iam_policy_document.kms.json
}

data "aws_iam_policy_document" "kms" {
  #checkov:skip=CKV_AWS_109:The key policy's root statement is the standard delegation to IAM; it is scoped to this one key
  #checkov:skip=CKV_AWS_111:The key policy's root statement is the standard delegation to IAM; it is scoped to this one key
  #checkov:skip=CKV_AWS_356:In a key policy "*" means this key only
  statement {
    sid       = "AccountAdmin"
    actions   = ["kms:*"]
    resources = ["*"]
    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"]
    }
  }
}

resource "aws_kms_alias" "lake" {
  name          = "alias/adl-${local.suffix}"
  target_key_id = aws_kms_key.lake.key_id
}

# ---------------------------------------------------------------- lake (S3)
resource "aws_s3_bucket" "lake" {
  bucket_prefix = "adl-${local.suffix}-lake-"
  force_destroy = var.environment == "dev"
}

resource "aws_s3_bucket" "access_logs" {
  bucket_prefix = "adl-${local.suffix}-logs-"
  force_destroy = var.environment == "dev"
}

resource "aws_s3_bucket_public_access_block" "lake" {
  for_each                = { lake = aws_s3_bucket.lake.id, logs = aws_s3_bucket.access_logs.id }
  bucket                  = each.value
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "lake" {
  for_each = { lake = aws_s3_bucket.lake.id, logs = aws_s3_bucket.access_logs.id }
  bucket   = each.value
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_versioning" "lake" {
  for_each = { lake = aws_s3_bucket.lake.id, logs = aws_s3_bucket.access_logs.id }
  bucket   = each.value
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "lake" {
  for_each = { lake = aws_s3_bucket.lake.id, logs = aws_s3_bucket.access_logs.id }
  bucket   = each.value
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.lake.arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "lake" {
  for_each = { lake = aws_s3_bucket.lake.id, logs = aws_s3_bucket.access_logs.id }
  bucket   = each.value
  rule {
    id     = "expire-old-versions"
    status = "Enabled"
    filter {}
    noncurrent_version_expiration {
      noncurrent_days = 30
    }
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

resource "aws_s3_bucket_logging" "lake" {
  bucket        = aws_s3_bucket.lake.id
  target_bucket = aws_s3_bucket.access_logs.id
  target_prefix = "lake/"
}

resource "aws_s3_bucket_policy" "tls_only" {
  for_each = { lake = aws_s3_bucket.lake, logs = aws_s3_bucket.access_logs }
  bucket   = each.value.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "DenyInsecureTransport"
      Effect    = "Deny"
      Principal = "*"
      Action    = "s3:*"
      Resource  = [each.value.arn, "${each.value.arn}/*"]
      Condition = { Bool = { "aws:SecureTransport" = "false" } }
    }]
  })
}

# ---------------------------------------------------------------- catalog and query (Glue + Athena)
resource "aws_glue_catalog_database" "layer" {
  for_each     = local.layers
  name         = "${var.domain}_${each.key}"
  location_uri = "s3://${aws_s3_bucket.lake.bucket}/${each.key}/"
}

resource "aws_athena_workgroup" "this" {
  name          = "adl-${local.suffix}"
  force_destroy = var.environment == "dev"

  configuration {
    enforce_workgroup_configuration    = true
    publish_cloudwatch_metrics_enabled = true
    bytes_scanned_cutoff_per_query     = 10737418240

    result_configuration {
      output_location = "s3://${aws_s3_bucket.lake.bucket}/athena-results/"
      encryption_configuration {
        encryption_option = "SSE_KMS"
        kms_key_arn       = aws_kms_key.lake.arn
      }
    }
  }
}

# ---------------------------------------------------------------- identities
resource "aws_iam_openid_connect_provider" "github" {
  count          = var.create_github_oidc_provider ? 1 : 0
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
}

data "aws_iam_policy_document" "github_trust" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [local.oidc_provider_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${var.github_repository}:environment:${var.environment}"]
    }
  }
}

resource "aws_iam_role" "pipeline" {
  name               = "adl-pipeline-${local.suffix}"
  assume_role_policy = data.aws_iam_policy_document.github_trust.json
}

data "aws_iam_policy_document" "pipeline" {
  statement {
    sid       = "LakeReadWrite"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket"]
    resources = [aws_s3_bucket.lake.arn, "${aws_s3_bucket.lake.arn}/*"]
  }
  statement {
    sid       = "Catalog"
    actions   = ["glue:GetDatabase", "glue:GetTable", "glue:GetTables", "glue:CreateTable", "glue:UpdateTable"]
    resources = concat(["arn:aws:glue:${var.region}:${data.aws_caller_identity.current.account_id}:catalog"], [for d in aws_glue_catalog_database.layer : d.arn], [for d in aws_glue_catalog_database.layer : "arn:aws:glue:${var.region}:${data.aws_caller_identity.current.account_id}:table/${d.name}/*"])
  }
  statement {
    sid       = "Key"
    actions   = ["kms:Decrypt", "kms:GenerateDataKey"]
    resources = [aws_kms_key.lake.arn]
  }
}

resource "aws_iam_role_policy" "pipeline" {
  name   = "lake-read-write"
  role   = aws_iam_role.pipeline.id
  policy = data.aws_iam_policy_document.pipeline.json
}

data "aws_iam_policy_document" "agents_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "agents" {
  name               = "adl-agents-${local.suffix}"
  assume_role_policy = data.aws_iam_policy_document.agents_trust.json
}

# Agents read the gold prefix and run queries in the one workgroup; bronze, silver and PII are out of reach by IAM.
data "aws_iam_policy_document" "agents" {
  statement {
    sid       = "GoldRead"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.lake.arn}/gold/*"]
  }
  statement {
    sid       = "GoldList"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.lake.arn]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["gold/*"]
    }
  }
  statement {
    sid       = "AthenaResults"
    actions   = ["s3:GetObject", "s3:PutObject"]
    resources = ["${aws_s3_bucket.lake.arn}/athena-results/*"]
  }
  statement {
    sid       = "Query"
    actions   = ["athena:StartQueryExecution", "athena:GetQueryExecution", "athena:GetQueryResults"]
    resources = [aws_athena_workgroup.this.arn]
  }
  statement {
    sid       = "GoldCatalog"
    actions   = ["glue:GetDatabase", "glue:GetTable", "glue:GetTables"]
    resources = ["arn:aws:glue:${var.region}:${data.aws_caller_identity.current.account_id}:catalog", aws_glue_catalog_database.layer["gold"].arn, "arn:aws:glue:${var.region}:${data.aws_caller_identity.current.account_id}:table/${aws_glue_catalog_database.layer["gold"].name}/*"]
  }
  statement {
    sid       = "Key"
    actions   = ["kms:Decrypt", "kms:GenerateDataKey"]
    resources = [aws_kms_key.lake.arn]
  }
}

resource "aws_iam_role_policy" "agents" {
  name   = "gold-read"
  role   = aws_iam_role.agents.id
  policy = data.aws_iam_policy_document.agents.json
}
