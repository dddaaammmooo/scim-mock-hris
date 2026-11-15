import os

import boto3
import pytest
from moto import mock_aws

os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ["AUTO_SEED"] = "true"
os.environ["MOCKHRIS_API_KEY"] = ""


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    from mockhris import settings
    from mockhris.store import Store, set_store

    settings.reset()
    with mock_aws():
        set_store(Store(resource=boto3.resource("dynamodb", region_name="us-east-1"), prefix="test"))
        from mockhris.app import create_app
        # Host must be localhost for the MCP endpoint's DNS-rebinding protection.
        with TestClient(create_app(), base_url="http://localhost") as c:
            yield c
        set_store(None)
