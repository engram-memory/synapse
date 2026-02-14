"""Synapse message bus — pub/sub engine with channel management."""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Callable

from synapse.models import Channel, MessageType, Priority, SynapseMessage
from synapse.registry import AgentRegistry
from synapse.storage import MessageStore

log = logging.getLogger(__name__)

Callback = Callable[[SynapseMessage], None]
AsyncCallback = Callable[[SynapseMessage], object]  # returns Awaitable


class Subscription:
    """A single subscription to a channel."""

    def __init__(
        self,
        agent_name: str,
        channel: str,
        callback: AsyncCallback | Callback | None = None,
        priority_min: Priority = Priority.BACKGROUND,
        type_filter: MessageType | None = None,
    ):
        self.agent_name = agent_name
        self.channel = channel
        self.callback = callback
        self.priority_min = priority_min
        self.type_filter = type_filter

    def matches(self, msg: SynapseMessage) -> bool:
        if msg.priority.value > self.priority_min.value:
            return False
        if self.type_filter and msg.type != self.type_filter:
            return False
        return True


class SynapseBus:
    """Core pub/sub message bus."""

    def __init__(self, registry: AgentRegistry | None = None, storage: MessageStore | None = None):
        self.registry = registry or AgentRegistry()
        self.storage = storage
        self._channels: dict[str, Channel] = {}
        self._subscriptions: dict[str, list[Subscription]] = defaultdict(list)
        self._message_history: list[SynapseMessage] = []
        self._max_history = 1000
        self._inbox: dict[str, list[SynapseMessage]] = defaultdict(list)
        self._max_inbox = 100

        # Default channels
        for name, desc in [
            ("#alerts", "Critical notifications — crashes, stop-loss, system errors"),
            ("#market-data", "TESS market signals, prices, sentiment"),
            ("#learning", "Genesis insights, patterns, knowledge"),
            ("#commands", "Direct instructions (user → agent)"),
            ("#heartbeat", "Agent health (automatic)"),
            ("#system", "Bus internal events"),
        ]:
            self.create_channel(name, desc)

    def create_channel(self, name: str, description: str = "") -> Channel:
        if name not in self._channels:
            self._channels[name] = Channel(name=name, description=description)
            log.info("Channel created: %s", name)
        return self._channels[name]

    def get_channel(self, name: str) -> Channel | None:
        return self._channels.get(name)

    def list_channels(self) -> list[Channel]:
        return list(self._channels.values())

    def subscribe(
        self,
        agent_name: str,
        channel: str,
        callback: AsyncCallback | Callback | None = None,
        priority_min: Priority = Priority.BACKGROUND,
        type_filter: MessageType | None = None,
    ) -> Subscription:
        # Auto-create channel if needed
        self.create_channel(channel)

        sub = Subscription(
            agent_name=agent_name,
            channel=channel,
            callback=callback,
            priority_min=priority_min,
            type_filter=type_filter,
        )
        self._subscriptions[channel].append(sub)
        log.info("Subscription: %s → %s", agent_name, channel)
        return sub

    def unsubscribe(self, agent_name: str, channel: str | None = None) -> int:
        removed = 0
        channels = [channel] if channel else list(self._subscriptions.keys())
        for ch in channels:
            before = len(self._subscriptions[ch])
            self._subscriptions[ch] = [
                s for s in self._subscriptions[ch] if s.agent_name != agent_name
            ]
            removed += before - len(self._subscriptions[ch])
        return removed

    async def publish(self, msg: SynapseMessage) -> int:
        # Auto-create channel
        self.create_channel(msg.channel)

        # Update channel stats
        self._channels[msg.channel].message_count += 1

        # Store in history
        self._message_history.append(msg)
        if len(self._message_history) > self._max_history:
            self._message_history = self._message_history[-self._max_history:]

        # Deliver to subscribers
        delivered = 0
        for sub in self._subscriptions.get(msg.channel, []):
            if not sub.matches(msg):
                continue

            # Store in agent inbox
            inbox = self._inbox[sub.agent_name]
            inbox.append(msg)
            if len(inbox) > self._max_inbox:
                self._inbox[sub.agent_name] = inbox[-self._max_inbox:]

            # Fire callback if registered
            if sub.callback:
                try:
                    result = sub.callback(msg)
                    if asyncio.iscoroutine(result):
                        await result
                except Exception:
                    log.exception("Callback error for %s on %s", sub.agent_name, msg.channel)

            delivered += 1

        # Persist to SQLite
        if self.storage:
            self.storage.store(msg)

        log.info(
            "Published to %s: [%s/%s] from %s → %d subscribers",
            msg.channel, msg.type.value, msg.priority.name, msg.sender, delivered,
        )
        return delivered

    def publish_sync(self, msg: SynapseMessage) -> int:
        """Synchronous publish — runs the async version in an event loop."""
        try:
            loop = asyncio.get_running_loop()
            # Already in async context, schedule it
            future = asyncio.ensure_future(self.publish(msg))
            return 0  # Can't wait synchronously in async context
        except RuntimeError:
            return asyncio.run(self.publish(msg))

    def get_inbox(self, agent_name: str, limit: int = 20, channel: str | None = None) -> list[SynapseMessage]:
        msgs = self._inbox.get(agent_name, [])
        if channel:
            msgs = [m for m in msgs if m.channel == channel]
        return msgs[-limit:]

    def clear_inbox(self, agent_name: str) -> int:
        count = len(self._inbox.get(agent_name, []))
        self._inbox[agent_name] = []
        return count

    def get_history(
        self,
        channel: str | None = None,
        limit: int = 50,
        since: datetime | None = None,
    ) -> list[SynapseMessage]:
        msgs = self._message_history
        if channel:
            msgs = [m for m in msgs if m.channel == channel]
        if since:
            msgs = [m for m in msgs if m.timestamp >= since]
        return msgs[-limit:]

    def stats(self) -> dict:
        return {
            "channels": len(self._channels),
            "subscriptions": sum(len(s) for s in self._subscriptions.values()),
            "agents_online": len(self.registry.list_agents()),
            "messages_total": sum(c.message_count for c in self._channels.values()),
            "history_size": len(self._message_history),
        }
