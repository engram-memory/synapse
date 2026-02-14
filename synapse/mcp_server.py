"""Synapse MCP server — gives Claude Code access to the message bus."""

from __future__ import annotations

import json
import logging
import sys

from synapse.client import SynapseClient

log = logging.getLogger(__name__)

# MCP protocol constants
JSONRPC_VERSION = "2.0"
MCP_PROTOCOL_VERSION = "2024-11-05"

client: SynapseClient | None = None
_registered: bool = False


def get_client() -> SynapseClient:
    """Lazy client + one-shot bus registration on first tool call."""
    global client, _registered
    if client is None:
        client = SynapseClient("claude-code", "http://localhost:8200")
    if not _registered:
        try:
            client.register(
                capabilities=["chat", "coding", "orchestration"],
                channels=["#alerts", "#commands", "#system"],
            )
            _registered = True
        except Exception as e:
            log.warning("Synapse bus registration deferred: %s", e)
    return client


# --- Tool definitions ---

TOOLS = [
    {
        "name": "synapse_publish",
        "description": (
            "Publish a message to a Synapse channel. Channels: #alerts, "
            "#market-data, #learning, #commands, #heartbeat, #system "
            "(or any custom channel)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "channel": {"type": "string", "description": "Channel name (e.g. #alerts)"},
                "message": {"type": "string", "description": "Message text or JSON payload"},
                "type": {
                    "type": "string",
                    "default": "event",
                    "enum": ["alert", "data", "command", "query", "response", "event"],
                },
                "priority": {
                    "type": "integer",
                    "enum": [0, 1, 2, 3, 4],
                    "default": 2,
                    "description": "0=CRITICAL, 1=HIGH, 2=NORMAL, 3=LOW, 4=BACKGROUND",
                },
            },
            "required": ["channel", "message"],
        },
    },
    {
        "name": "synapse_inbox",
        "description": "Read messages from the Claude Code inbox on the Synapse bus.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 10},
                "channel": {"type": "string", "description": "Filter by channel (optional)"},
            },
        },
    },
    {
        "name": "synapse_agents",
        "description": "List all agents registered on the Synapse bus.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "synapse_channels",
        "description": "List all channels on the Synapse bus.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "synapse_history",
        "description": "Get message history for a Synapse channel.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "channel": {"type": "string", "description": "Channel name"},
                "limit": {"type": "integer", "default": 20},
            },
            "required": ["channel"],
        },
    },
    {
        "name": "synapse_alert",
        "description": "Send a critical alert to all agents via #alerts channel.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "Alert message"},
            },
            "required": ["message"],
        },
    },
    {
        "name": "synapse_command",
        "description": "Send a command to a specific agent via #commands channel.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "Target agent name"},
                "action": {"type": "string", "description": "Action to perform"},
            },
            "required": ["target", "action"],
        },
    },
]


def handle_tool_call(name: str, arguments: dict) -> str:
    """Execute a Synapse tool and return the result as text."""
    c = get_client()

    try:
        if name == "synapse_publish":
            payload = arguments["message"]
            try:
                payload = json.loads(payload)
            except (json.JSONDecodeError, TypeError):
                payload = {"message": payload}

            from synapse.models import MessageType, Priority

            result = c.publish(
                channel=arguments["channel"],
                payload=payload,
                type=MessageType(arguments.get("type", "event")),
                priority=Priority(arguments.get("priority", 2)),
            )
            return json.dumps(result, indent=2)

        elif name == "synapse_inbox":
            msgs = c.inbox(
                limit=arguments.get("limit", 10),
                channel=arguments.get("channel"),
            )
            if not msgs:
                return "Inbox is empty."
            return json.dumps(msgs, indent=2)

        elif name == "synapse_agents":
            agents = c.list_agents()
            if not agents:
                return "No agents registered."
            return json.dumps(agents, indent=2)

        elif name == "synapse_channels":
            channels = c.channels()
            return json.dumps(channels, indent=2)

        elif name == "synapse_history":
            msgs = c.history(
                channel=arguments["channel"],
                limit=arguments.get("limit", 20),
            )
            if not msgs:
                return f"No messages in {arguments['channel']}."
            return json.dumps(msgs, indent=2)

        elif name == "synapse_alert":
            result = c.alert(arguments["message"])
            return json.dumps(result, indent=2)

        elif name == "synapse_command":
            result = c.command(
                target=arguments["target"],
                action=arguments["action"],
            )
            return json.dumps(result, indent=2)

        else:
            return f"Unknown tool: {name}"

    except ConnectionError as e:
        return f"Error: Synapse bus not reachable. Is the server running? ({e})"
    except Exception as e:
        return f"Error: {e}"


# --- MCP stdio protocol ---


def send_response(id: int | str, result: dict):
    msg = json.dumps({"jsonrpc": JSONRPC_VERSION, "id": id, "result": result})
    sys.stdout.write(f"{msg}\n")
    sys.stdout.flush()


def send_error(id: int | str, code: int, message: str):
    error = {"code": code, "message": message}
    msg = json.dumps({"jsonrpc": JSONRPC_VERSION, "id": id, "error": error})
    sys.stdout.write(f"{msg}\n")
    sys.stdout.flush()


def run_stdio():
    """Run as MCP server over stdio (for Claude Code integration)."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue

        req_id = request.get("id")
        method = request.get("method", "")

        if method == "initialize":
            send_response(
                req_id,
                {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "synapse", "version": "0.1.0"},
                },
            )

        elif method == "notifications/initialized":
            pass  # No response needed

        elif method == "tools/list":
            send_response(req_id, {"tools": TOOLS})

        elif method == "tools/call":
            params = request.get("params", {})
            tool_name = params.get("name", "")
            arguments = params.get("arguments", {})
            result_text = handle_tool_call(tool_name, arguments)
            send_response(
                req_id,
                {
                    "content": [{"type": "text", "text": result_text}],
                },
            )

        elif method == "resources/list":
            send_response(req_id, {"resources": []})

        elif method == "prompts/list":
            send_response(req_id, {"prompts": []})

        elif method == "ping":
            send_response(req_id, {})

        elif method.startswith("notifications/"):
            pass  # Notifications don't need responses

        elif req_id is not None:
            send_error(req_id, -32601, f"Method not found: {method}")


if __name__ == "__main__":
    run_stdio()
