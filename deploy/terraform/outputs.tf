output "url" {
  description = "Public URL of MockHRIS (UI at /, API docs at /docs, MCP at /mcp)."
  value       = local.public_url
}

output "alb_dns_name" {
  description = "DNS name of the load balancer (create a CNAME/alias to it for domain_name)."
  value       = aws_lb.this.dns_name
}

output "cluster_name" {
  description = "ECS cluster name."
  value       = aws_ecs_cluster.this.name
}

output "service_name" {
  description = "ECS service name."
  value       = aws_ecs_service.app.name
}

output "log_group" {
  description = "CloudWatch log group."
  value       = aws_cloudwatch_log_group.app.name
}

output "table_names" {
  description = "DynamoDB table names."
  value       = [for t in aws_dynamodb_table.this : t.name]
}

output "api_key_secret_arn" {
  description = "Secrets Manager ARN holding the MockHRIS API key (null when require_api_key is false)."
  value       = var.require_api_key ? aws_secretsmanager_secret.api_key[0].arn : null
}

output "get_api_key_command" {
  description = "AWS CLI command that prints the API key."
  value       = var.require_api_key ? "aws secretsmanager get-secret-value --region ${var.aws_region} --secret-id ${aws_secretsmanager_secret.api_key[0].arn} --query SecretString --output text" : null
}

output "mcp_url" {
  description = "MCP endpoint URL."
  value       = "${local.public_url}/mcp"
}
