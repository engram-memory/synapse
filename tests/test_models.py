"""Tests for Synapse data models."""

from synapse.models import (
    AgentInfo,
    AgentStatus,
    Channel,
    MessageType,
    Priority,
    SynapseMessage,
)


def test_message_creation():
    msg = SynapseMessage(
        channel="#alerts",
        sender="test-agent",
        payload={"message": "hello"},
    )
    assert msg.channel == "#alerts"
    assert msg.sender == "test-agent"
    assert msg.type == MessageType.EVENT
    assert msg.priority == Priority.NORMAL
    assert msg.ttl == 0
    assert msg.reply_to is None
    assert len(msg.id) == 12


def test_message_to_dict():
    msg = SynapseMessage(
        channel="#system",
        sender="test",
        payload={"key": "value"},
        type=MessageType.ALERT,
        priority=Priority.CRITICAL,
    )
    d = msg.to_dict()
    assert d["channel"] == "#system"
    assert d["sender"] == "test"
    assert d["type"] == "alert"
    assert d["priority"] == 0
    assert d["payload"] == {"key": "value"}
    assert "timestamp" in d


def test_message_roundtrip():
    original = SynapseMessage(
        channel="#alerts",
        sender="agent-a",
        payload={"data": 42},
        type=MessageType.DATA,
        priority=Priority.HIGH,
        ttl=300,
        reply_to="abc123",
    )
    d = original.to_dict()
    restored = SynapseMessage.from_dict(d)
    assert restored.channel == original.channel
    assert restored.sender == original.sender
    assert restored.payload == original.payload
    assert restored.type == original.type
    assert restored.priority == original.priority
    assert restored.ttl == original.ttl
    assert restored.reply_to == original.reply_to
    assert restored.id == original.id


def test_message_types():
    for t in MessageType:
        assert isinstance(t.value, str)
    assert MessageType("alert") == MessageType.ALERT
    assert MessageType("event") == MessageType.EVENT


def test_priority_ordering():
    assert Priority.CRITICAL.value < Priority.HIGH.value
    assert Priority.HIGH.value < Priority.NORMAL.value
    assert Priority.NORMAL.value < Priority.LOW.value
    assert Priority.LOW.value < Priority.BACKGROUND.value


def test_agent_info():
    agent = AgentInfo(name="test", capabilities=["chat"], channels=["#alerts"])
    assert agent.status == AgentStatus.ONLINE
    d = agent.to_dict()
    assert d["name"] == "test"
    assert d["capabilities"] == ["chat"]
    assert d["status"] == "online"


def test_channel():
    ch = Channel(name="#test", description="Test channel")
    d = ch.to_dict()
    assert d["name"] == "#test"
    assert d["message_count"] == 0
