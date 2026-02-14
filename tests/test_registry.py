"""Tests for AgentRegistry."""

from synapse.models import AgentStatus
from synapse.registry import AgentRegistry


def test_register():
    reg = AgentRegistry()
    agent = reg.register("test-agent", capabilities=["chat"])
    assert agent.name == "test-agent"
    assert agent.capabilities == ["chat"]
    assert agent.status == AgentStatus.ONLINE


def test_re_register_updates():
    reg = AgentRegistry()
    reg.register("agent", capabilities=["v1"])
    agent = reg.register("agent", capabilities=["v2"])
    assert agent.capabilities == ["v2"]
    assert agent.status == AgentStatus.ONLINE


def test_heartbeat_known():
    reg = AgentRegistry()
    reg.register("agent")
    assert reg.heartbeat("agent") is True


def test_heartbeat_unknown_auto_registers():
    reg = AgentRegistry()
    assert reg.heartbeat("unknown") is True
    assert reg.get("unknown") is not None
    assert reg.get("unknown").status == AgentStatus.ONLINE


def test_unregister():
    reg = AgentRegistry()
    reg.register("agent")
    assert reg.unregister("agent") is True
    assert reg.get("agent").status == AgentStatus.OFFLINE


def test_unregister_unknown():
    reg = AgentRegistry()
    assert reg.unregister("nope") is False


def test_list_agents():
    reg = AgentRegistry()
    reg.register("a")
    reg.register("b")
    assert len(reg.list_agents()) == 2


def test_list_agents_by_status():
    reg = AgentRegistry()
    reg.register("online")
    reg.register("offline")
    reg.unregister("offline")
    online = reg.list_agents(status=AgentStatus.ONLINE)
    assert len(online) == 1
    assert online[0].name == "online"


def test_find_by_capability():
    reg = AgentRegistry()
    reg.register("trader", capabilities=["trading", "analysis"])
    reg.register("chatbot", capabilities=["chat"])
    traders = reg.find_by_capability("trading")
    assert len(traders) == 1
    assert traders[0].name == "trader"


def test_check_timeouts():
    reg = AgentRegistry(heartbeat_timeout=0)  # Instant timeout
    reg.register("agent")
    import time

    time.sleep(0.01)
    timed_out = reg.check_timeouts()
    assert "agent" in timed_out
    assert reg.get("agent").status == AgentStatus.OFFLINE
