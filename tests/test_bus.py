"""Tests for SynapseBus."""

import pytest
from synapse.bus import SynapseBus, Subscription
from synapse.models import MessageType, Priority, SynapseMessage
from synapse.registry import AgentRegistry


@pytest.fixture
def bus():
    return SynapseBus(registry=AgentRegistry())


def test_default_channels(bus):
    channels = bus.list_channels()
    names = [c.name for c in channels]
    assert "#alerts" in names
    assert "#system" in names
    assert "#heartbeat" in names


def test_create_channel(bus):
    ch = bus.create_channel("#custom", "My channel")
    assert ch.name == "#custom"
    assert ch.description == "My channel"


def test_create_channel_idempotent(bus):
    bus.create_channel("#test", "First")
    bus.create_channel("#test", "Second")
    ch = bus.get_channel("#test")
    assert ch.description == "First"  # Not overwritten


def test_subscribe(bus):
    sub = bus.subscribe("agent-a", "#alerts")
    assert sub.agent_name == "agent-a"
    assert sub.channel == "#alerts"


def test_unsubscribe(bus):
    bus.subscribe("agent-a", "#alerts")
    bus.subscribe("agent-a", "#system")
    removed = bus.unsubscribe("agent-a")
    assert removed == 2


def test_unsubscribe_specific_channel(bus):
    bus.subscribe("agent-a", "#alerts")
    bus.subscribe("agent-a", "#system")
    removed = bus.unsubscribe("agent-a", "#alerts")
    assert removed == 1


@pytest.mark.asyncio
async def test_publish(bus):
    bus.subscribe("agent-a", "#alerts")
    msg = SynapseMessage(channel="#alerts", sender="test", payload={"data": 1})
    delivered = await bus.publish(msg)
    assert delivered == 1


@pytest.mark.asyncio
async def test_publish_updates_channel_stats(bus):
    msg = SynapseMessage(channel="#alerts", sender="test", payload={})
    await bus.publish(msg)
    ch = bus.get_channel("#alerts")
    assert ch.message_count == 1


@pytest.mark.asyncio
async def test_inbox(bus):
    bus.subscribe("agent-a", "#alerts")
    msg = SynapseMessage(channel="#alerts", sender="test", payload={"x": 1})
    await bus.publish(msg)
    inbox = bus.get_inbox("agent-a")
    assert len(inbox) == 1
    assert inbox[0].payload == {"x": 1}


@pytest.mark.asyncio
async def test_inbox_filter_by_channel(bus):
    bus.subscribe("agent-a", "#alerts")
    bus.subscribe("agent-a", "#system")
    await bus.publish(SynapseMessage(channel="#alerts", sender="t", payload={}))
    await bus.publish(SynapseMessage(channel="#system", sender="t", payload={}))
    alerts = bus.get_inbox("agent-a", channel="#alerts")
    assert len(alerts) == 1


@pytest.mark.asyncio
async def test_clear_inbox(bus):
    bus.subscribe("agent-a", "#alerts")
    await bus.publish(SynapseMessage(channel="#alerts", sender="t", payload={}))
    count = bus.clear_inbox("agent-a")
    assert count == 1
    assert len(bus.get_inbox("agent-a")) == 0


@pytest.mark.asyncio
async def test_history(bus):
    await bus.publish(SynapseMessage(channel="#alerts", sender="t", payload={"n": 1}))
    await bus.publish(SynapseMessage(channel="#alerts", sender="t", payload={"n": 2}))
    history = bus.get_history(channel="#alerts")
    assert len(history) == 2


@pytest.mark.asyncio
async def test_stats(bus):
    bus.subscribe("agent-a", "#alerts")
    await bus.publish(SynapseMessage(channel="#alerts", sender="t", payload={}))
    stats = bus.stats()
    assert stats["messages_total"] >= 1
    assert stats["subscriptions"] >= 1


def test_subscription_matches():
    sub = Subscription("a", "#alerts", priority_min=Priority.NORMAL)
    msg_high = SynapseMessage(channel="#alerts", sender="t", payload={}, priority=Priority.HIGH)
    msg_low = SynapseMessage(channel="#alerts", sender="t", payload={}, priority=Priority.LOW)
    assert sub.matches(msg_high) is True
    assert sub.matches(msg_low) is False


def test_subscription_type_filter():
    sub = Subscription("a", "#alerts", type_filter=MessageType.ALERT)
    msg_alert = SynapseMessage(channel="#alerts", sender="t", payload={}, type=MessageType.ALERT)
    msg_event = SynapseMessage(channel="#alerts", sender="t", payload={}, type=MessageType.EVENT)
    assert sub.matches(msg_alert) is True
    assert sub.matches(msg_event) is False


@pytest.mark.asyncio
async def test_callback(bus):
    received = []
    bus.subscribe("agent-a", "#alerts", callback=lambda msg: received.append(msg))
    msg = SynapseMessage(channel="#alerts", sender="t", payload={"cb": True})
    await bus.publish(msg)
    assert len(received) == 1
    assert received[0].payload == {"cb": True}
