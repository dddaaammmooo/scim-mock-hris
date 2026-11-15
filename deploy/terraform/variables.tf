############################################################################
# Required
############################################################################

variable "allowed_cidrs" {
  description = "IPv4 CIDR blocks allowed to reach the load balancer (HTTP/HTTPS). There is deliberately no default: choose your own egress IP (for example [\"203.0.113.10/32\"]). Use [\"0.0.0.0/0\"] only together with an API key and HTTPS."
  type        = list(string)

  validation {
    condition     = length(var.allowed_cidrs) > 0 && alltrue([for c in var.allowed_cidrs : can(cidrhost(c, 0))])
    error_message = "allowed_cidrs must be a non-empty list of valid IPv4 CIDR blocks."
  }
}

############################################################################
# General
############################################################################

variable "aws_region" {
  description = "AWS region to deploy into."
  type        = string
  default     = "us-east-1"
}

variable "name" {
  description = "Name prefix for all resources (ECS cluster/service, ALB, IAM roles, ...). Lower-case letters, digits and hyphens."
  type        = string
  default     = "mockhris"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,19}$", var.name))
    error_message = "name must start with a letter, be 2-20 characters, and contain only lower-case letters, digits and hyphens."
  }
}

variable "tags" {
  description = "Extra tags applied to every resource through the provider default_tags."
  type        = map(string)
  default     = {}
}

############################################################################
# Container image / sizing
############################################################################

variable "image" {
  description = "Container image to run. The default is the public image published by CI to GitHub Container Registry; override to pin a digest or use a private mirror."
  type        = string
  default     = "ghcr.io/dddaaammmooo/scim-mock-hris:latest"
}

variable "cpu" {
  description = "Fargate task CPU units (256 = 0.25 vCPU)."
  type        = number
  default     = 256

  validation {
    condition     = contains([256, 512, 1024, 2048, 4096], var.cpu)
    error_message = "cpu must be one of 256, 512, 1024, 2048, 4096."
  }
}

variable "memory" {
  description = "Fargate task memory in MiB. Must be a valid combination for the chosen cpu."
  type        = number
  default     = 512
}

variable "desired_count" {
  description = "Number of tasks. Data lives in DynamoDB, so more than one task is fine (each keeps a short-lived in-memory cache)."
  type        = number
  default     = 1

  validation {
    condition     = var.desired_count >= 1
    error_message = "desired_count must be at least 1."
  }
}

variable "log_retention_days" {
  description = "CloudWatch Logs retention in days."
  type        = number
  default     = 14

  validation {
    condition     = contains([1, 3, 5, 7, 14, 30, 60, 90, 120, 150, 180, 365, 400, 545, 731, 1096, 1827, 2192, 2557, 2922, 3288, 3653], var.log_retention_days)
    error_message = "log_retention_days must be a value CloudWatch Logs accepts (for example 7, 14, 30, 90, 365)."
  }
}

variable "enable_container_insights" {
  description = "Enable ECS Container Insights (extra CloudWatch cost)."
  type        = bool
  default     = false
}

variable "enable_execute_command" {
  description = "Allow `aws ecs execute-command` into the task (adds SSM permissions to the task role)."
  type        = bool
  default     = false
}

############################################################################
# Application settings
############################################################################

variable "auto_seed" {
  description = "Seed the fictitious company on startup when the users table is empty (AUTO_SEED)."
  type        = bool
  default     = true
}

variable "table_prefix" {
  description = "DynamoDB table prefix (TABLE_PREFIX). Defaults to var.name. Table names are <prefix>-users, <prefix>-groups, ... and must be unique per account and region."
  type        = string
  default     = null
}

variable "require_api_key" {
  description = "Require MOCKHRIS_API_KEY on /api and /mcp. Strongly recommended whenever the service is reachable from the internet."
  type        = bool
  default     = true
}

variable "api_key" {
  description = "MockHRIS API key. Leave null to have Terraform generate one (stored in Secrets Manager and in Terraform state)."
  type        = string
  default     = null
  sensitive   = true
}

variable "integration_tokens" {
  description = "Optional SCIM bearer tokens for outbound integrations, as { INTEGRATION_ID = token }. Each becomes INTEGRATION_TOKEN_<ID> (upper-case, '-' replaced by '_'), sourced from Secrets Manager. Example: { GITHUB_EMU = \"...\" } or { HRIS_TARGET = \"<scim-target token>\" }."
  type        = map(string)
  default     = {}
  sensitive   = true
}

variable "extra_environment" {
  description = "Additional plain (non-secret) environment variables for the container."
  type        = map(string)
  default     = {}
}

variable "secret_recovery_window_days" {
  description = "Secrets Manager recovery window on delete. Use 0 to delete immediately (handy for throwaway stacks; allows quick re-create with the same name)."
  type        = number
  default     = 7

  validation {
    condition     = var.secret_recovery_window_days == 0 || (var.secret_recovery_window_days >= 7 && var.secret_recovery_window_days <= 30)
    error_message = "secret_recovery_window_days must be 0 or between 7 and 30."
  }
}

############################################################################
# DynamoDB
############################################################################

variable "enable_pitr" {
  description = "Enable DynamoDB point-in-time recovery on every table."
  type        = bool
  default     = true
}

variable "dynamodb_deletion_protection" {
  description = "Enable DynamoDB deletion protection. When true, set it back to false (and apply) before `terraform destroy`."
  type        = bool
  default     = false
}

############################################################################
# Load balancer / DNS
############################################################################

variable "certificate_arn" {
  description = "ACM certificate ARN (same region). When set, an HTTPS listener is created and HTTP redirects to HTTPS."
  type        = string
  default     = null
}

variable "domain_name" {
  description = "Optional custom host name that you point at the ALB (CNAME/alias, managed outside this module). Used for PUBLIC_BASE_URL and MCP_ALLOWED_HOSTS. Must match the certificate when certificate_arn is set."
  type        = string
  default     = null
}

variable "extra_mcp_allowed_hosts" {
  description = "Extra Host header patterns for MCP_ALLOWED_HOSTS (the ALB DNS name and domain_name are added automatically)."
  type        = list(string)
  default     = []
}

variable "alb_deletion_protection" {
  description = "Enable deletion protection on the ALB."
  type        = bool
  default     = false
}

############################################################################
# Networking
############################################################################

variable "create_vpc" {
  description = "Create a small dedicated VPC (2 public subnets, plus 2 private subnets when use_nat_gateway). Set false to deploy into an existing VPC and provide vpc_id, alb_subnet_ids and task_subnet_ids."
  type        = bool
  default     = true
}

variable "vpc_cidr" {
  description = "CIDR of the VPC created when create_vpc is true."
  type        = string
  default     = "10.42.0.0/16"
}

variable "use_nat_gateway" {
  description = "Only with create_vpc. true: tasks run in private subnets behind a single NAT gateway (about 32 USD/month plus data). false (default, cheapest): tasks run in the public subnets with a public IP; their security group still only accepts traffic from the ALB."
  type        = bool
  default     = false
}

variable "create_dynamodb_vpc_endpoint" {
  description = "Only with create_vpc. Create a free DynamoDB gateway endpoint so DynamoDB traffic stays on the AWS network (and does not use the NAT gateway)."
  type        = bool
  default     = true
}

variable "vpc_id" {
  description = "Existing VPC ID (when create_vpc is false)."
  type        = string
  default     = null
}

variable "alb_subnet_ids" {
  description = "Existing public subnet IDs, in at least 2 AZs, for the ALB (when create_vpc is false)."
  type        = list(string)
  default     = []
}

variable "task_subnet_ids" {
  description = "Existing subnet IDs for the Fargate tasks (when create_vpc is false). They need a route to the internet (or VPC endpoints) to pull the image and reach Secrets Manager, CloudWatch Logs and DynamoDB."
  type        = list(string)
  default     = []
}

variable "assign_public_ip" {
  description = "Give tasks a public IP (when create_vpc is false). Needed if task_subnet_ids are public subnets without a NAT gateway."
  type        = bool
  default     = false
}
