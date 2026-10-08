# Offline plan tests: mocked provider, no AWS credentials, nothing created.
mock_provider "aws" {
  mock_data "aws_caller_identity" {
    defaults = {
      account_id = "123456789012"
    }
  }
  mock_data "aws_iam_policy_document" {
    defaults = {
      json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}"
    }
  }
}

override_resource {
  target          = aws_kms_key.lake
  override_during = plan
  values = {
    arn = "arn:aws:kms:us-east-1:123456789012:key/mock"
  }
}

run "dev_defaults" {
  command = plan

  variables {
    environment = "dev"
  }

  assert {
    condition     = alltrue([for b in aws_s3_bucket_public_access_block.lake : b.block_public_acls && b.block_public_policy && b.restrict_public_buckets])
    error_message = "no bucket can be made public"
  }

  assert {
    condition     = aws_kms_key.lake.enable_key_rotation
    error_message = "the lake key rotates"
  }

  assert {
    condition     = length(aws_glue_catalog_database.layer) == 3 && aws_glue_catalog_database.layer["gold"].name == "retail_gold"
    error_message = "one Glue database per layer"
  }

  assert {
    condition     = aws_athena_workgroup.this.configuration[0].enforce_workgroup_configuration
    error_message = "the workgroup enforces encrypted results"
  }
}

run "rejects_unknown_environment" {
  command = plan

  variables {
    environment = "qa"
  }

  expect_failures = [var.environment]
}
