"""Process-wide service accessors shared by the API, MCP server and CLI."""
from .hr import HRService
from .integrations import Integrations, Runner
from .store import get_store


def hr():
    return HRService(get_store())


def integrations():
    return Integrations(get_store(), hr())


def engine(integration_id, http=None, draft=None):
    return runner(http).engine(integration_id, draft)


def runner(http=None):
    return Runner(get_store(), hr(), http)
