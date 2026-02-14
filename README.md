# Synapse

**Agent-to-Agent Communication Layer** — Real-time message bus for AI agents.

Synapse is a lightweight pub/sub message bus designed for multi-agent AI systems. It provides real-time communication between AI agents via REST API, WebSocket streaming, and MCP (Model Context Protocol) integration.

## Features

- **Pub/Sub Message Bus** — Channel-based messaging with priority levels and message types
- **Agent Registry** — Auto-discovery, heartbeat monitoring, capability-based routing
- **WebSocket Streaming** — Real-time push to connected agents
- **SQLite Persistence** — Messages survive restarts, queryable history
- **MCP Server** — Native integration with Claude Code and other MCP clients
- **Web Dashboard** — Live monitoring at `/dashboard`
- **Zero Dependencies Client** — `SynapseClient` uses only stdlib (`urllib`)

## Quick Start

```bash
pip install synapse-bus
```

### Start the Server

```bash
synapse-server
# → Synapse bus running on http://localhost:8200
```

### Connect an Agent

```python
from synapse import SynapseClient, Priority

client = SynapseClient("my-agent")
client.register(capabilities=["analysis"], channels=["#alerts"])

# Publish a message
client.publish("#alerts", {"status": "online"})

# Send a critical alert
client.alert("Something went wrong!")

# Read inbox
messages = client.inbox()

# Get bus health
print(client.health())
```

### Use as MCP Server

Add to your Claude Code config (`~/.claude.json`):

```json
{
  "mcpServers": {
    "synapse": {
      "command": "synapse-mcp",
      "args": []
    }
  }
}
```

This gives Claude Code 7 tools: `synapse_publish`, `synapse_inbox`, `synapse_agents`, `synapse_channels`, `synapse_history`, `synapse_alert`, `synapse_command`.

## Architecture

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   Agent A    │     │   Agent B    │     │   Agent C    │
│  (Trading)   │     │  (Analysis)  │     │  (Assistant) │
└──────┬───────┘     └──────┬───────┘     └──────┬───────┘
       │ HTTP/WS            │ HTTP/WS            │ MCP
       └────────────┬───────┴────────────────────┘
                    │
            ┌───────▼───────┐
            │  Synapse Bus   │
            │   (FastAPI)    │
            ├────────────────┤
            │  #alerts       │
            │  #market-data  │
            │  #commands     │
            │  #system       │
            │  #heartbeat    │
            │  (custom...)   │
            ├────────────────┤
            │  SQLite Store  │
            └────────────────┘
```

## API

### REST Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/agents/register` | Register an agent |
| `POST` | `/agents/{name}/heartbeat` | Send heartbeat |
| `GET` | `/agents` | List all agents |
| `GET` | `/agents/{name}` | Get agent details |
| `POST` | `/publish` | Publish a message |
| `POST` | `/subscribe` | Subscribe to a channel |
| `GET` | `/inbox/{agent}` | Get agent inbox |
| `GET` | `/channels` | List channels |
| `GET` | `/history/{channel}` | Get message history |
| `GET` | `/health` | Bus health & stats |
| `GET` | `/dashboard` | Web dashboard |

### WebSocket

Connect to `/ws/{agent_name}` for real-time streaming:

```javascript
const ws = new WebSocket("ws://localhost:8200/ws/my-agent");
ws.onmessage = (event) => console.log(JSON.parse(event.data));

// Publish via WebSocket
ws.send(JSON.stringify({
  action: "publish",
  channel: "#alerts",
  payload: { message: "Hello from WS" }
}));
```

### Message Model

```python
SynapseMessage(
    channel="#alerts",          # Target channel
    sender="my-agent",         # Sender name
    payload={"key": "value"},  # Arbitrary dict
    type=MessageType.EVENT,    # alert|data|command|query|response|event
    priority=Priority.NORMAL,  # 0=CRITICAL, 1=HIGH, 2=NORMAL, 3=LOW, 4=BACKGROUND
    ttl=0,                     # Seconds until expiry (0=never)
)
```

## Default Channels

| Channel | Purpose |
|---------|---------|
| `#alerts` | Critical notifications |
| `#market-data` | Trading signals, prices |
| `#learning` | AI insights, patterns |
| `#commands` | Direct instructions |
| `#heartbeat` | Agent health (automatic) |
| `#system` | Bus internal events |

## Development

```bash
git clone https://github.com/engram-memory/synapse.git
cd synapse
pip install -e ".[dev]"
pytest
```

## Part of the Engram Ecosystem

Synapse is the communication backbone for [Engram](https://github.com/engram-memory/engram) — the universal memory layer for AI agents. Together they enable persistent, communicating multi-agent systems.

## License

MIT
