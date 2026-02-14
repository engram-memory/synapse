"""Core data models for Synapse message bus."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class MessageType(str, Enum):
    ALERT = "alert"
    DATA = "data"
    COMMAND = "command"
    QUERY = "query"
    RESPONSE = "response"
    EVENT = "event"


class Priority(int, Enum):
    CRITICAL = 0
    HIGH = 1
    NORMAL = 2
    LOW = 3
    BACKGROUND = 4


class AgentStatus(str, Enum):
    ONLINE = "online"
    OFFLINE = "offline"
    BUSY = "busy"


@dataclass
class SynapseMessage:
    channel: str
    sender: str
    payload: dict
    type: MessageType = MessageType.EVENT
    priority: Priority = Priority.NORMAL
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    ttl: int = 0  # 0 = no expiry
    reply_to: str | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "channel": self.channel,
            "sender": self.sender,
            "type": self.type.value,
            "priority": self.priority.value,
            "payload": self.payload,
            "timestamp": self.timestamp.isoformat(),
            "ttl": self.ttl,
            "reply_to": self.reply_to,
        }

    @classmethod
    def from_dict(cls, data: dict) -> SynapseMessage:
        return cls(
            id=data["id"],
            channel=data["channel"],
            sender=data["sender"],
            type=MessageType(data["type"]),
            priority=Priority(data["priority"]),
            payload=data["payload"],
            timestamp=datetime.fromisoformat(data["timestamp"]),
            ttl=data.get("ttl", 0),
            reply_to=data.get("reply_to"),
        )


@dataclass
class AgentInfo:
    name: str
    capabilities: list[str] = field(default_factory=list)
    channels: list[str] = field(default_factory=list)
    status: AgentStatus = AgentStatus.ONLINE
    registered_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_heartbeat: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "capabilities": self.capabilities,
            "channels": self.channels,
            "status": self.status.value,
            "registered_at": self.registered_at.isoformat(),
            "last_heartbeat": self.last_heartbeat.isoformat(),
            "metadata": self.metadata,
        }


@dataclass
class Channel:
    name: str
    description: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    message_count: int = 0

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "created_at": self.created_at.isoformat(),
            "message_count": self.message_count,
        }
