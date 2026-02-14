"""Agent registry — discovery, heartbeat, status tracking."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from synapse.models import AgentInfo, AgentStatus

log = logging.getLogger(__name__)


class AgentRegistry:
    """Tracks registered agents and their status."""

    def __init__(self, heartbeat_timeout: int = 60):
        self._agents: dict[str, AgentInfo] = {}
        self._heartbeat_timeout = heartbeat_timeout

    def register(
        self,
        name: str,
        capabilities: list[str] | None = None,
        channels: list[str] | None = None,
        metadata: dict | None = None,
    ) -> AgentInfo:
        if name in self._agents:
            agent = self._agents[name]
            agent.status = AgentStatus.ONLINE
            agent.last_heartbeat = datetime.now(timezone.utc)
            if capabilities is not None:
                agent.capabilities = capabilities
            if channels is not None:
                agent.channels = channels
            if metadata is not None:
                agent.metadata = metadata
            log.info("Agent re-registered: %s", name)
            return agent

        agent = AgentInfo(
            name=name,
            capabilities=capabilities or [],
            channels=channels or [],
            metadata=metadata or {},
        )
        self._agents[name] = agent
        log.info("Agent registered: %s (capabilities=%s)", name, agent.capabilities)
        return agent

    def unregister(self, name: str) -> bool:
        if name in self._agents:
            self._agents[name].status = AgentStatus.OFFLINE
            log.info("Agent unregistered: %s", name)
            return True
        return False

    def heartbeat(self, name: str) -> bool:
        if name in self._agents:
            self._agents[name].last_heartbeat = datetime.now(timezone.utc)
            self._agents[name].status = AgentStatus.ONLINE
            return True
        # Auto-register unknown agents on heartbeat (resilience after restart)
        self.register(name=name)
        log.info("Agent auto-registered via heartbeat: %s", name)
        return True

    def get(self, name: str) -> AgentInfo | None:
        return self._agents.get(name)

    def list_agents(self, status: AgentStatus | None = None) -> list[AgentInfo]:
        agents = list(self._agents.values())
        if status is not None:
            agents = [a for a in agents if a.status == status]
        return agents

    def find_by_capability(self, capability: str) -> list[AgentInfo]:
        return [
            a
            for a in self._agents.values()
            if capability in a.capabilities and a.status == AgentStatus.ONLINE
        ]

    def check_timeouts(self) -> list[str]:
        now = datetime.now(timezone.utc)
        timed_out = []
        for agent in self._agents.values():
            if agent.status == AgentStatus.ONLINE:
                delta = (now - agent.last_heartbeat).total_seconds()
                if delta > self._heartbeat_timeout:
                    agent.status = AgentStatus.OFFLINE
                    timed_out.append(agent.name)
                    log.warning("Agent timed out: %s (%.0fs)", agent.name, delta)
        return timed_out
