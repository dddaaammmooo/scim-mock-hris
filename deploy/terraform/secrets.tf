locals {
  # Integration token env var names: upper-case, '-' -> '_' (matches MockHRIS behaviour).
  integration_token_ids = nonsensitive(toset(keys(var.integration_tokens)))
}

resource "random_password" "api_key" {
  count = var.require_api_key && var.api_key == null ? 1 : 0

  length  = 40
  special = false
}

resource "aws_secretsmanager_secret" "api_key" {
  count = var.require_api_key ? 1 : 0

  name                    = "${var.name}/api-key"
  description             = "MockHRIS API key (X-API-Key or Authorization: Bearer)"
  recovery_window_in_days = var.secret_recovery_window_days
}

resource "aws_secretsmanager_secret_version" "api_key" {
  count = var.require_api_key ? 1 : 0

  secret_id     = aws_secretsmanager_secret.api_key[0].id
  secret_string = var.api_key != null ? var.api_key : random_password.api_key[0].result
}

resource "aws_secretsmanager_secret" "integration_token" {
  for_each = local.integration_token_ids

  name                    = "${var.name}/integration-token/${each.value}"
  description             = "SCIM bearer token for MockHRIS integration ${each.value}"
  recovery_window_in_days = var.secret_recovery_window_days
}

resource "aws_secretsmanager_secret_version" "integration_token" {
  for_each = local.integration_token_ids

  secret_id     = aws_secretsmanager_secret.integration_token[each.value].id
  secret_string = var.integration_tokens[each.value]
}
