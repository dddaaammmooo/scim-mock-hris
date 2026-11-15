# Tables are named <prefix>-<name>; hash keys mirror TABLES in mockhris/store.py.
locals {
  table_prefix = coalesce(var.table_prefix, var.name)

  tables = {
    users        = "email"
    groups       = "name"
    settings     = "key"
    integrations = "id"
    "scim-state" = "key"
    "sync-runs"  = "id"
    "mock-scim"  = "id"
  }
}

resource "aws_dynamodb_table" "this" {
  for_each = local.tables

  name                        = "${local.table_prefix}-${each.key}"
  billing_mode                = "PAY_PER_REQUEST"
  hash_key                    = each.value
  deletion_protection_enabled = var.dynamodb_deletion_protection

  attribute {
    name = each.value
    type = "S"
  }

  point_in_time_recovery {
    enabled = var.enable_pitr
  }

  server_side_encryption {
    enabled = true # AWS managed KMS key (aws/dynamodb)
  }
}
