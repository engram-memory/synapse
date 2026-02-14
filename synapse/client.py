"""Synapse client — connect to the bus from any Python agent."""

from __future__ import annotations

import json
import logging
from typing import Any

import urllib.request
import urllib.error

from synapse.models import MessageType, Priority, SynapseMessage

log = logging.getLogger(__name__)

DEFAULT_URL = "http://localhost:8200"


class SynapseClient:
    """Lightweight HTTP client for the Synapse bus."""

    def __init__(self, agent_name: str, base_url: str = DEFAULT_URL):
        self.agent_name = agent_name
        self.base_url = base_url.rstrip("/")

    def _request(self, method: str, path: str, data: dict | None = None) -> dict:
        url = f"{self.base_url}{path}"
        body = json.dumps(data).encode() if data else None
        req = urllib.request.Request(
            url,
            data=body,
            method=method,
            headers={"Content-Type": "application/json"} if body else {},
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            log.error("Synapse API error: %s %s → %s: %s", method, path, e.code, body)
            raise
        except urllib.error.URLError as e:
            log.error("Synapse unreachable: %s", e.reason)
            raise ConnectionError(f"Synapse bus not reachable at {self.base_url}") from e

    # --- Agent management ---

    def register(
        self,
        capabilities: list[str] | None = None,
        channels: list[str] | None = None,
        metadata: dict | None = None,
    ) -> dict:
        return self._request("POST", "/agents/register", {
            "name": self.agent_name,
            "capabilities": capabilities or [],
            "channels": channels or [],
            "metadata": metadata or {},
        })

    def heartbeat(self) -> dict:
        return self._request("POST", f"/agents/{self.agent_name}/heartbeat")

    def unregister(self) -> dict:
        return self._request("DELETE", f"/agents/{self.agent_name}")

    def list_agents(self) -> list[dict]:
        return self._request("GET", "/agents")["agents"]

    # --- Publish / Subscribe ---

    def publish(
        self,
        channel: str,
        payload: dict | str,
        type: MessageType = MessageType.EVENT,
        priority: Priority = Priority.NORMAL,
        ttl: int = 0,
        reply_to: str | None = None,
    ) -> dict:
        if isinstance(payload, str):
            payload = {"message": payload}
        return self._request("POST", "/publish", {
            "channel": channel,
            "sender": self.agent_name,
            "payload": payload,
            "type": type.value,
            "priority": priority.value,
            "ttl": ttl,
            "reply_to": reply_to,
        })

    def subscribe(self, channel: str, priority_min: Priority = Priority.BACKGROUND) -> dict:
        return self._request("POST", "/subscribe", {
            "agent_name": self.agent_name,
            "channel": channel,
            "priority_min": priority_min.value,
        })

    # --- Inbox ---

    def inbox(self, limit: int = 20, channel: str | None = None) -> list[dict]:
        from urllib.parse import quote
        path = f"/inbox/{self.agent_name}?limit={limit}"
        if channel:
            path += f"&channel={quote(channel, safe='')}"
        return self._request("GET", path)["messages"]

    def clear_inbox(self) -> dict:
        return self._request("DELETE", f"/inbox/{self.agent_name}")

    # --- Channels ---

    def channels(self) -> list[dict]:
        return self._request("GET", "/channels")["channels"]

    def history(self, channel: str, limit: int = 50) -> list[dict]:
        from urllib.parse import quote
        return self._request("GET", f"/history/{quote(channel, safe='')}?limit={limit}")["messages"]

    # --- Health ---

    def health(self) -> dict:
        return self._request("GET", "/health")

    # --- Convenience methods ---

    def alert(self, message: str, **extra: Any) -> dict:
        """Publish a critical alert."""
        return self.publish(
            "#alerts",
            {"message": message, **extra},
            type=MessageType.ALERT,
            priority=Priority.CRITICAL,
        )

    def command(self, target: str, action: str, **params: Any) -> dict:
        """Send a command to a specific agent channel."""
        return self.publish(
            "#commands",
            {"target": target, "action": action, **params},
            type=MessageType.COMMAND,
            priority=Priority.HIGH,
        )

    def query(self, channel: str, question: str, **params: Any) -> dict:
        """Publish a query expecting a response."""
        return self.publish(
            channel,
            {"question": question, **params},
            type=MessageType.QUERY,
            priority=Priority.NORMAL,
        )
