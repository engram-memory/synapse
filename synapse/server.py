"""Synapse HTTP server — REST API + WebSocket for the message bus."""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from synapse.bus import SynapseBus
from synapse.models import AgentStatus, MessageType, Priority, SynapseMessage
from synapse.registry import AgentRegistry
from synapse.storage import MessageStore

log = logging.getLogger(__name__)

# Global bus instance
registry = AgentRegistry(heartbeat_timeout=120)
store = MessageStore()
bus = SynapseBus(registry=registry, storage=store)

# WebSocket connections per agent
ws_connections: dict[str, WebSocket] = {}


# Known agents — pre-registered on startup so they always appear on dashboard
KNOWN_AGENTS = [
    {"name": "claude-code", "capabilities": ["chat", "coding", "orchestration"], "channels": ["#alerts", "#commands", "#system"]},
    {"name": "tess", "capabilities": ["trading", "analysis", "signals"], "channels": ["#alerts", "#market-data", "#heartbeat"]},
    {"name": "jarvis", "capabilities": ["assistant", "telegram", "notifications"], "channels": ["#alerts", "#commands", "#system", "#heartbeat"]},
    {"name": "genesis", "capabilities": ["cognition", "reasoning", "knowledge", "learning"], "channels": ["#alerts", "#system", "#learning", "#heartbeat"]},
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Synapse bus starting on port 8200")
    app.state.started_at = datetime.now(timezone.utc).isoformat()
    # Pre-register known agents (they start offline until heartbeat)
    for agent_def in KNOWN_AGENTS:
        agent = registry.register(**agent_def)
        agent.status = AgentStatus.OFFLINE  # Offline until real heartbeat
        for ch in agent_def["channels"]:
            bus.subscribe(agent_def["name"], ch)
    log.info("Pre-registered %d known agents", len(KNOWN_AGENTS))
    # Start heartbeat checker
    task = asyncio.create_task(_heartbeat_loop())
    yield
    task.cancel()
    log.info("Synapse bus stopped")


app = FastAPI(
    title="Synapse",
    description="Agent-to-Agent Communication Layer",
    version="0.1.0",
    lifespan=lifespan,
)


# --- Pydantic models for API ---

class RegisterRequest(BaseModel):
    name: str
    capabilities: list[str] = []
    channels: list[str] = []
    metadata: dict = {}


class PublishRequest(BaseModel):
    channel: str
    sender: str
    payload: dict
    type: str = "event"
    priority: int = 2
    ttl: int = 0
    reply_to: str | None = None


class SubscribeRequest(BaseModel):
    agent_name: str
    channel: str
    priority_min: int = 4


STATIC_DIR = Path(__file__).parent / "static"


# --- Dashboard ---

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    html_file = STATIC_DIR / "dashboard.html"
    return HTMLResponse(html_file.read_text(encoding="utf-8"))


# --- Agent endpoints ---

@app.post("/agents/register")
async def register_agent(req: RegisterRequest):
    agent = registry.register(
        name=req.name,
        capabilities=req.capabilities,
        channels=req.channels,
        metadata=req.metadata,
    )
    # Auto-subscribe to requested channels
    for ch in req.channels:
        bus.subscribe(req.name, ch)
    return {"status": "registered", "agent": agent.to_dict()}


@app.post("/agents/{name}/heartbeat")
async def agent_heartbeat(name: str):
    registry.heartbeat(name)  # Auto-registers if unknown
    return {"status": "ok"}


@app.delete("/agents/{name}")
async def unregister_agent(name: str):
    if not registry.unregister(name):
        raise HTTPException(404, f"Agent '{name}' not found")
    bus.unsubscribe(name)
    return {"status": "unregistered"}


@app.get("/agents")
async def list_agents(status: str | None = None):
    s = AgentStatus(status) if status else None
    agents = registry.list_agents(status=s)
    return {"agents": [a.to_dict() for a in agents]}


@app.get("/agents/{name}")
async def get_agent(name: str):
    agent = registry.get(name)
    if not agent:
        raise HTTPException(404, f"Agent '{name}' not found")
    return agent.to_dict()


@app.get("/agents/capability/{capability}")
async def find_by_capability(capability: str):
    agents = registry.find_by_capability(capability)
    return {"agents": [a.to_dict() for a in agents]}


# --- Message endpoints ---

@app.post("/publish")
async def publish_message(req: PublishRequest):
    msg = SynapseMessage(
        channel=req.channel,
        sender=req.sender,
        payload=req.payload,
        type=MessageType(req.type),
        priority=Priority(req.priority),
        ttl=req.ttl,
        reply_to=req.reply_to,
    )
    delivered = await bus.publish(msg)

    # Push to WebSocket subscribers
    ws_delivered = await _ws_broadcast(msg)

    return {
        "status": "published",
        "id": msg.id,
        "delivered": delivered,
        "ws_delivered": ws_delivered,
    }


@app.post("/subscribe")
async def subscribe_channel(req: SubscribeRequest):
    sub = bus.subscribe(
        agent_name=req.agent_name,
        channel=req.channel,
        priority_min=Priority(req.priority_min),
    )
    return {"status": "subscribed", "agent": sub.agent_name, "channel": sub.channel}


@app.get("/inbox/{agent_name}")
async def get_inbox(agent_name: str, limit: int = 20, channel: str | None = None):
    msgs = bus.get_inbox(agent_name, limit=limit, channel=channel)
    return {"messages": [m.to_dict() for m in msgs], "count": len(msgs)}


@app.delete("/inbox/{agent_name}")
async def clear_inbox(agent_name: str):
    count = bus.clear_inbox(agent_name)
    return {"cleared": count}


# --- Channel endpoints ---

@app.get("/channels")
async def list_channels():
    channels = bus.list_channels()
    return {"channels": [c.to_dict() for c in channels]}


@app.post("/channels/{name}")
async def create_channel(name: str, description: str = ""):
    ch = bus.create_channel(name, description)
    return ch.to_dict()


@app.get("/history/{channel}")
async def get_history(channel: str, limit: int = 50):
    # Try persistent storage first, fallback to in-memory
    if store:
        msgs = store.get_history(channel=channel, limit=limit)
        if msgs:
            return {"messages": [m.to_dict() for m in msgs], "count": len(msgs), "source": "sqlite"}
    msgs = bus.get_history(channel=channel, limit=limit)
    return {"messages": [m.to_dict() for m in msgs], "count": len(msgs), "source": "memory"}


# --- Stats ---

@app.get("/health")
async def health():
    result = {
        "status": "ok",
        "version": "0.1.0",
        "started_at": getattr(app.state, "started_at", None),
        **bus.stats(),
    }
    if store:
        result["storage"] = store.stats()
    return result


# --- WebSocket for real-time streaming ---

@app.websocket("/ws/{agent_name}")
async def websocket_endpoint(websocket: WebSocket, agent_name: str):
    await websocket.accept()
    ws_connections[agent_name] = websocket
    log.info("WebSocket connected: %s", agent_name)

    # Auto-register if not registered
    registry.register(name=agent_name)

    try:
        while True:
            data = await websocket.receive_text()
            try:
                parsed = json.loads(data)
                # Handle incoming publish via WebSocket
                if parsed.get("action") == "publish":
                    msg = SynapseMessage(
                        channel=parsed["channel"],
                        sender=agent_name,
                        payload=parsed.get("payload", {}),
                        type=MessageType(parsed.get("type", "event")),
                        priority=Priority(parsed.get("priority", 2)),
                    )
                    await bus.publish(msg)
                    await _ws_broadcast(msg)
                elif parsed.get("action") == "subscribe":
                    bus.subscribe(agent_name, parsed["channel"])
                elif parsed.get("action") == "heartbeat":
                    registry.heartbeat(agent_name)
            except (json.JSONDecodeError, KeyError, ValueError) as e:
                await websocket.send_text(json.dumps({"error": str(e)}))
    except WebSocketDisconnect:
        log.info("WebSocket disconnected: %s", agent_name)
        ws_connections.pop(agent_name, None)


async def _ws_broadcast(msg: SynapseMessage) -> int:
    """Push message to all WebSocket-connected agents subscribed to the channel."""
    delivered = 0
    data = json.dumps(msg.to_dict())
    dead = []

    for agent_name, ws in ws_connections.items():
        # Check if agent is subscribed to this channel
        subs = bus._subscriptions.get(msg.channel, [])
        if any(s.agent_name == agent_name and s.matches(msg) for s in subs):
            try:
                await ws.send_text(data)
                delivered += 1
            except Exception:
                dead.append(agent_name)

    for name in dead:
        ws_connections.pop(name, None)

    return delivered


async def _heartbeat_loop():
    """Periodically check for timed-out agents."""
    while True:
        await asyncio.sleep(30)
        timed_out = registry.check_timeouts()
        if timed_out:
            msg = SynapseMessage(
                channel="#system",
                sender="synapse",
                type=MessageType.EVENT,
                priority=Priority.HIGH,
                payload={"event": "agents_timed_out", "agents": timed_out},
            )
            await bus.publish(msg)


def run(host: str = "0.0.0.0", port: int = 8200):
    """Start the Synapse server."""
    import uvicorn
    uvicorn.run(app, host=host, port=port, log_level="info")
