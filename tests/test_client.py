"""Tests for SynapseClient (unit tests — no server needed)."""

from synapse.client import SynapseClient
from synapse.models import MessageType, Priority


def test_client_init():
    c = SynapseClient("test-agent")
    assert c.agent_name == "test-agent"
    assert c.base_url == "http://localhost:8200"


def test_client_custom_url():
    c = SynapseClient("agent", base_url="http://custom:9000/")
    assert c.base_url == "http://custom:9000"  # Trailing slash stripped
