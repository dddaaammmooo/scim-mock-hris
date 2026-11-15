"""Process configuration from environment variables."""
import os
from dataclasses import dataclass, field


def _bool(name, default):
    v = os.environ.get(name)
    return default if v is None else v.strip().lower() in ("1", "true", "yes", "on")


def _list(name):
    return [x.strip() for x in os.environ.get(name, "").split(",") if x.strip()]


@dataclass
class Settings:
    # DynamoDB. Empty endpoint = real AWS DynamoDB using the default credential chain.
    dynamodb_endpoint: str = field(default_factory=lambda: os.environ.get("DYNAMODB_ENDPOINT", "http://localhost:8001"))
    aws_region: str = field(default_factory=lambda: os.environ.get("AWS_REGION", "us-east-1"))
    table_prefix: str = field(default_factory=lambda: os.environ.get("TABLE_PREFIX", "mockhris"))
    # Seed the synthetic company on startup when the users table is empty.
    auto_seed: bool = field(default_factory=lambda: _bool("AUTO_SEED", True))
    # Optional shared secret for /api and /mcp (header X-API-Key or "Authorization: Bearer").
    api_key: str = field(default_factory=lambda: os.environ.get("MOCKHRIS_API_KEY", ""))
    # SCIM bearer token. When set it overrides any token saved through the GUI.
    github_scim_token: str = field(default_factory=lambda: os.environ.get("GITHUB_SCIM_TOKEN", ""))
    # Token the built-in mock GitHub SCIM target accepts.
    mock_scim_token: str = field(default_factory=lambda: os.environ.get("MOCK_SCIM_TOKEN", "mock-scim-token"))
    # Base URL the app uses to reach itself (default SCIM target = built-in mock).
    public_base_url: str = field(default_factory=lambda: os.environ.get("PUBLIC_BASE_URL", "http://localhost:8000"))
    # Extra Host header values allowed by the MCP endpoint's DNS-rebinding protection.
    mcp_allowed_hosts: list = field(default_factory=lambda: _list("MCP_ALLOWED_HOSTS"))


_settings = None


def get():
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reset():
    global _settings
    _settings = None
