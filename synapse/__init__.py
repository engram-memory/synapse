"""Synapse — Agent-to-Agent Communication Layer."""

__version__ = "0.1.0"

from synapse.bus import SynapseBus
from synapse.client import SynapseClient
from synapse.models import (
    AgentInfo,
    AgentStatus,
    Channel,
    MessageType,
    Priority,
    SynapseMessage,
)
from synapse.registry import AgentRegistry
from synapse.storage import MessageStore

__all__ = [
    "AgentInfo",
    "AgentStatus",
    "Channel",
    "MessageType",
    "Priority",
    "SynapseMessage",
    "SynapseClient",
    "SynapseBus",
    "AgentRegistry",
    "MessageStore",
]
