"""Browser tests: the real app under uvicorn (in a thread) on moto's in-memory DynamoDB, driven by Playwright.

Run with:  pytest -m ui --no-cov            (bundled chromium; `python -m playwright install chromium` once)
           PLAYWRIGHT_CHANNEL=msedge pytest -m ui --no-cov   (use an installed Edge/Chrome instead)
"""
import os
import socket
import threading
import time
from pathlib import Path

import boto3
import httpx
import pytest
import uvicorn
from moto import mock_aws
from playwright.sync_api import expect

expect.set_options(timeout=15_000)


def pytest_collection_modifyitems(items):
    here = str(Path(__file__).parent)
    for item in items:
        if str(item.fspath).startswith(here):
            item.add_marker(pytest.mark.ui)


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def server():
    """Seeded app on a free port. The GitHub EMU integration targets this same server's built-in mock."""
    os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
    os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    os.environ["PUBLIC_BASE_URL"] = base
    os.environ["AUTO_SEED"] = "true"
    os.environ.pop("MOCKHRIS_API_KEY", None)
    from mockhris import settings
    from mockhris.store import Store, set_store
    settings.reset()
    with mock_aws():
        set_store(Store(resource=boto3.resource("dynamodb", region_name="us-east-1"), prefix="ui"))
        from mockhris.app import create_app
        srv = uvicorn.Server(uvicorn.Config(create_app(), host="127.0.0.1", port=port, log_level="warning"))
        thread = threading.Thread(target=srv.run, daemon=True)
        thread.start()
        deadline = time.time() + 60
        while not srv.started:
            assert thread.is_alive() and time.time() < deadline, "uvicorn failed to start"
            time.sleep(0.05)
        api = httpx.Client(base_url=base, timeout=120)
        yield type("Server", (), {"base": base, "api": api})
        srv.should_exit = True
        thread.join(timeout=10)
        set_store(None)
    os.environ.pop("PUBLIC_BASE_URL", None)
    settings.reset()


@pytest.fixture(scope="session")
def browser_type_launch_args(browser_type_launch_args):
    channel = os.environ.get("PLAYWRIGHT_CHANNEL")
    return {**browser_type_launch_args, **({"channel": channel} if channel else {})}


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args):
    return {**browser_context_args, "viewport": {"width": 1440, "height": 900}, "color_scheme": "dark",
            "accept_downloads": True}


@pytest.fixture()
def ui(page, server):
    """Page helper; fails the test if the page logged any JavaScript error.

    Dialogs (confirm/prompt) are answered from `page.answers`: True accepts, False dismisses, a string is
    typed into a prompt. With no queued answer, dialogs are accepted.
    """
    errors = []
    page.answers = []
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    # Browsers log every non-2xx fetch as "Failed to load resource"; the tests trigger validation errors
    # (422) on purpose, so only real script errors count.
    page.on("console", lambda m: m.type == "error" and not m.text.startswith("Failed to load resource")
            and errors.append(f"console.error: {m.text}"))

    def handle(dialog):
        a = page.answers.pop(0) if page.answers else True
        if a is False:
            dialog.dismiss()
        elif isinstance(a, str):
            dialog.accept(a)
        else:
            dialog.accept()
    page.on("dialog", handle)
    page.set_default_timeout(15000)

    def go(hash_="#/dashboard"):
        page.goto(f"{server.base}/ui/{hash_}")
        page.wait_for_selector("#main > :not(.loading)")
    page.go = go
    page.server = server
    yield page
    assert errors == [], errors
