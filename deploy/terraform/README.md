# MockHRIS on AWS (Terraform)

Self-contained root module that runs MockHRIS on ECS Fargate behind an Application Load Balancer, with DynamoDB for storage. The full walkthrough is in [docs/deploy-aws.md](../../docs/deploy-aws.md).

## Quick start

```bash
cd deploy/terraform
cp terraform.tfvars.example terraform.tfvars   # set allowed_cidrs at minimum
terraform init
terraform plan
terraform apply
```

## What it creates

| File | Resources |
|---|---|
| `network.tf` | VPC, 2 public subnets (2 AZs), optional 2 private subnets + 1 NAT gateway, DynamoDB gateway endpoint, security groups. Or reuse an existing VPC (`create_vpc = false`). |
| `alb.tf` | ALB, target group (health check `/api/health`), HTTP listener, optional HTTPS listener (HTTP then redirects). |
| `dynamodb.tf` | 7 on-demand tables `<prefix>-users`, `-groups`, `-settings`, `-integrations`, `-scim-state`, `-sync-runs`, `-mock-scim` with PITR and SSE. |
| `secrets.tf` | Secrets Manager secrets: API key (generated unless supplied) and optional integration tokens. |
| `iam.tf` | Execution role (logs, read the injected secrets) and task role (DynamoDB item actions on these tables only, plus `ListTables`). |
| `ecs.tf` | Log group, cluster, task definition, service with deployment circuit breaker and rollback. |

## Inputs you will usually set

| Variable | Required | Notes |
|---|---|---|
| `allowed_cidrs` | **yes** | No default. CIDRs allowed to reach the ALB, e.g. `["203.0.113.10/32"]`. |
| `aws_region` | no | Default `us-east-1`. |
| `image` | no | Default `ghcr.io/dddaaammmooo/scim-mock-hris:latest`; pin a tag for repeatable deploys. |
| `certificate_arn`, `domain_name` | no | Enable HTTPS and a custom host name. |
| `use_nat_gateway` | no | Default `false` (tasks in public subnets, reachable only from the ALB). |
| `api_key` | no | Generated if null. |

See `variables.tf` for the complete list. Outputs include `url`, `get_api_key_command`, `table_names`, `cluster_name`, `service_name` and `log_group`.

## Remote state (optional)

No backend is committed. To keep state in S3 add a block like this to `versions.tf` (bucket and table must already exist):

```hcl
# terraform {
#   backend "s3" {
#     bucket         = "my-terraform-state-bucket"
#     key            = "scim-mock-hris/terraform.tfstate"
#     region         = "us-east-1"
#     dynamodb_table = "my-terraform-locks"
#     encrypt        = true
#   }
# }
```

Terraform state contains the generated API key. Treat the state as a secret.

## Validate locally

```bash
terraform fmt -check -recursive
terraform init -backend=false
terraform validate
```
