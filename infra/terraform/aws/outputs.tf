output "LAKE_BUCKET" {
  description = "S3 bucket for the lake (adl.storage.aws.AwsStore bucket)."
  value       = aws_s3_bucket.lake.bucket
}

output "ATHENA_WORKGROUP" {
  description = "Athena workgroup the adapter queries in."
  value       = aws_athena_workgroup.this.name
}

output "PIPELINE_ROLE_ARN" {
  description = "Role GitHub Actions assumes through OIDC."
  value       = aws_iam_role.pipeline.arn
}

output "AGENT_ROLE_ARN" {
  description = "Role the agents run as (reads gold only)."
  value       = aws_iam_role.agents.arn
}
