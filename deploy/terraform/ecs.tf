locals {
  container_name = "mockhris"
  container_port = 8000
  health_path    = "/api/health"

  # Public URL: custom domain when given, otherwise the ALB DNS name.
  public_host = coalesce(var.domain_name, aws_lb.this.dns_name)
  public_url  = "${local.https_enabled ? "https" : "http"}://${local.public_host}"

  # MCP DNS-rebinding protection: allow the ALB name and the custom domain, with and without a port.
  mcp_allowed_hosts = distinct(concat(
    [aws_lb.this.dns_name, "${aws_lb.this.dns_name}:*"],
    var.domain_name == null ? [] : [var.domain_name, "${var.domain_name}:*"],
    var.extra_mcp_allowed_hosts,
  ))

  environment = merge(
    {
      DYNAMODB_ENDPOINT = "" # empty = real AWS DynamoDB via the task role
      AWS_REGION        = var.aws_region
      TABLE_PREFIX      = local.table_prefix
      AUTO_SEED         = tostring(var.auto_seed)
      PUBLIC_BASE_URL   = local.public_url
      MCP_ALLOWED_HOSTS = join(",", local.mcp_allowed_hosts)
    },
    var.extra_environment,
  )

  secret_env = merge(
    var.require_api_key ? { MOCKHRIS_API_KEY = aws_secretsmanager_secret.api_key[0].arn } : {},
    { for id in local.integration_token_ids :
    "INTEGRATION_TOKEN_${upper(replace(id, "-", "_"))}" => aws_secretsmanager_secret.integration_token[id].arn },
  )

  secret_arns = values(local.secret_env)
}

resource "aws_cloudwatch_log_group" "app" {
  name              = "/ecs/${var.name}"
  retention_in_days = var.log_retention_days
}

resource "aws_ecs_cluster" "this" {
  name = var.name

  setting {
    name  = "containerInsights"
    value = var.enable_container_insights ? "enabled" : "disabled"
  }
}

resource "aws_ecs_task_definition" "app" {
  family                   = var.name
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.cpu
  memory                   = var.memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  container_definitions = jsonencode([
    {
      name      = local.container_name
      image     = var.image
      essential = true

      portMappings = [{ containerPort = local.container_port, protocol = "tcp" }]

      environment = [for k, v in local.environment : { name = k, value = v }]
      secrets     = [for k, arn in local.secret_env : { name = k, valueFrom = arn }]

      # Mirrors the Dockerfile HEALTHCHECK (the image has no curl).
      healthCheck = {
        command     = ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:${local.container_port}${local.health_path}', timeout=2)"]
        interval    = 10
        timeout     = 3
        retries     = 5
        startPeriod = 20
      }

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.app.name
          awslogs-region        = var.aws_region
          awslogs-stream-prefix = "app"
        }
      }
    }
  ])
}

resource "aws_ecs_service" "app" {
  name             = var.name
  cluster          = aws_ecs_cluster.this.id
  task_definition  = aws_ecs_task_definition.app.arn
  desired_count    = var.desired_count
  launch_type      = "FARGATE"
  platform_version = "LATEST"

  enable_execute_command             = var.enable_execute_command
  health_check_grace_period_seconds  = 60
  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  network_configuration {
    subnets          = local.task_subnet_ids
    security_groups  = [aws_security_group.tasks.id]
    assign_public_ip = local.task_public_ip
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.app.arn
    container_name   = local.container_name
    container_port   = local.container_port
  }

  # Listener must exist before the service registers targets.
  depends_on = [aws_lb_listener.http, aws_lb_listener.https]
}
