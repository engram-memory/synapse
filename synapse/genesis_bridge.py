"""
Genesis → Synapse Bridge — Non-invasive HTTP polling bridge.

Polls the Genesis API (port 7777) for status changes and publishes
relevant events to the Synapse message bus. Zero modifications to Genesis code.

Events published:
- Health critical/degraded → #alerts (CRITICAL/HIGH)
- Emotion changes → #system (NORMAL)
- Layer status changes → #system (LOW)
- Degraded modules → #alerts (HIGH)
- Heartbeat (health, layers, emotions) → #heartbeat (LOW)
- Synapse inbox commands → forwarded to Genesis /chat
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from typing import Any

from synapse.client import SynapseClient
from synapse.models import MessageType, Priority

log = logging.getLogger("genesis_bridge")

# --- Config ---
GENESIS_URL = os.getenv("GENESIS_URL", "http://localhost:7777")
SYNAPSE_URL = os.getenv("SYNAPSE_URL", "http://localhost:8200")
POLL_INTERVAL = int(os.getenv("GENESIS_POLL_INTERVAL", "10"))
HEARTBEAT_CYCLES = 6  # heartbeat every 6 cycles (60s at 10s poll)


class GenesisBridge:
    """Polls Genesis API and publishes state changes to Synapse."""

    def __init__(
        self,
        genesis_url: str = GENESIS_URL,
        synapse_url: str = SYNAPSE_URL,
        poll_interval: int = POLL_INTERVAL,
    ):
        self.genesis_url = genesis_url.rstrip("/")
        self.poll_interval = poll_interval
        self.client = SynapseClient("genesis", synapse_url)
        self._running = False
        self._thread: threading.Thread | None = None

        # State tracking for change detection
        self._last_health: float | None = None
        self._last_emotions: dict = {}
        self._last_degraded: list = []
        self._last_layers: dict = {}
        self._genesis_online = False
        self._consecutive_failures = 0

    def start(self) -> bool:
        """Start the bridge as a background thread."""
        if self._running:
            return False

        # Register genesis on Synapse
        try:
            self.client.register(
                capabilities=["cognition", "reasoning", "knowledge", "learning"],
                channels=["#alerts", "#system", "#learning", "#heartbeat"],
            )
        except Exception as e:
            log.error("Cannot register on Synapse: %s", e)
            return False

        self._running = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True, name="genesis-bridge")
        self._thread.start()
        log.info(
            "Genesis bridge started — polling %s every %ds",
            self.genesis_url,
            self.poll_interval,
        )
        return True

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=10)
        log.info("Genesis bridge stopped")

    # --- HTTP helpers ---

    def _get_json(self, path: str) -> dict | None:
        """GET request to Genesis API, returns parsed JSON or None."""
        url = f"{self.genesis_url}{path}"
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=5) as resp:
                return json.loads(resp.read())
        except Exception:
            return None

    def _post_json(self, path: str, data: dict) -> dict | None:
        """POST request to Genesis API."""
        url = f"{self.genesis_url}{path}"
        try:
            body = json.dumps(data).encode()
            req = urllib.request.Request(
                url,
                data=body,
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read())
        except Exception:
            return None

    # --- Main loop ---

    def _poll_loop(self):
        """Main loop: poll Genesis status and forward to Synapse."""
        heartbeat_counter = 0

        while self._running:
            try:
                status = self._get_json("/status")

                if status is None:
                    self._handle_offline()
                else:
                    self._handle_online(status)

                # Heartbeat to Synapse
                heartbeat_counter += 1
                if heartbeat_counter >= HEARTBEAT_CYCLES:
                    self._send_heartbeat(status)
                    heartbeat_counter = 0

                # Keep genesis alive on Synapse bus
                try:
                    self.client.heartbeat()
                except Exception:
                    pass

                # Check Synapse inbox for commands
                self._check_inbox()

            except Exception as e:
                log.error("Bridge error: %s", e)

            time.sleep(self.poll_interval)

    def _handle_offline(self):
        """Handle Genesis being unreachable."""
        self._consecutive_failures += 1

        if self._genesis_online:
            # Was online, now offline
            self._genesis_online = False
            log.warning("Genesis went offline")
            try:
                self.client.publish(
                    "#alerts",
                    {
                        "event": "genesis_offline",
                        "message": "Genesis daemon is not responding",
                        "consecutive_failures": self._consecutive_failures,
                    },
                    type=MessageType.ALERT,
                    priority=Priority.HIGH,
                )
            except Exception:
                pass

    def _handle_online(self, status: dict):
        """Process Genesis status and detect changes."""
        self._consecutive_failures = 0

        if not self._genesis_online:
            # Was offline, now online
            self._genesis_online = True
            log.info("Genesis came online")
            try:
                self.client.publish(
                    "#system",
                    {
                        "event": "genesis_online",
                        "message": "Genesis daemon is now running",
                        "model": status.get("model", "unknown"),
                    },
                    type=MessageType.EVENT,
                    priority=Priority.NORMAL,
                )
            except Exception:
                pass

        # Check health changes
        self._check_health(status)

        # Check emotion changes
        self._check_emotions(status)

        # Check degraded modules
        self._check_degraded(status)

        # Check layer changes
        self._check_layers(status)

    def _check_health(self, status: dict):
        """Detect health changes and alert if critical."""
        health = status.get("health", 1.0)

        if self._last_health is not None:
            # Health dropped significantly
            if health < self._last_health - 0.15:
                priority = Priority.CRITICAL if health <= 0.3 else Priority.HIGH
                try:
                    self.client.publish(
                        "#alerts",
                        {
                            "event": "health_degraded",
                            "health": health,
                            "previous": self._last_health,
                            "drop": round(self._last_health - health, 2),
                        },
                        type=MessageType.ALERT,
                        priority=priority,
                    )
                    log.warning("Health dropped: %.2f → %.2f", self._last_health, health)
                except Exception as e:
                    log.error("Failed to publish health alert: %s", e)

            # Health recovered
            elif health > self._last_health + 0.15 and self._last_health < 0.5:
                try:
                    self.client.publish(
                        "#system",
                        {
                            "event": "health_recovered",
                            "health": health,
                            "previous": self._last_health,
                        },
                        type=MessageType.EVENT,
                        priority=Priority.NORMAL,
                    )
                    log.info("Health recovered: %.2f → %.2f", self._last_health, health)
                except Exception as e:
                    log.error("Failed to publish recovery: %s", e)

        self._last_health = health

    def _check_emotions(self, status: dict):
        """Detect significant emotion changes."""
        emotions = status.get("emotions", {})
        if not emotions or emotions == self._last_emotions:
            return

        # Check for significant changes (any emotion shifted > 0.2)
        changed = {}
        for emotion, value in emotions.items():
            if isinstance(value, (int, float)):
                prev = self._last_emotions.get(emotion, 0.5)
                if isinstance(prev, (int, float)) and abs(value - prev) > 0.2:
                    changed[emotion] = {"current": value, "previous": prev}

        if changed:
            try:
                self.client.publish(
                    "#system",
                    {
                        "event": "emotion_change",
                        "changes": changed,
                        "emotions": emotions,
                    },
                    type=MessageType.DATA,
                    priority=Priority.NORMAL,
                )
                log.info("Emotion change: %s", list(changed.keys()))
            except Exception as e:
                log.error("Failed to publish emotion change: %s", e)

        self._last_emotions = emotions.copy()

    def _check_degraded(self, status: dict):
        """Detect newly degraded modules."""
        degraded = status.get("degraded_modules", [])
        if degraded == self._last_degraded:
            return

        new_degraded = [m for m in degraded if m not in self._last_degraded]
        recovered = [m for m in self._last_degraded if m not in degraded]

        if new_degraded:
            try:
                self.client.publish(
                    "#alerts",
                    {
                        "event": "modules_degraded",
                        "degraded": new_degraded,
                        "total_degraded": len(degraded),
                    },
                    type=MessageType.ALERT,
                    priority=Priority.HIGH,
                )
                log.warning("Modules degraded: %s", new_degraded)
            except Exception as e:
                log.error("Failed to publish degraded alert: %s", e)

        if recovered:
            try:
                self.client.publish(
                    "#system",
                    {
                        "event": "modules_recovered",
                        "recovered": recovered,
                        "total_degraded": len(degraded),
                    },
                    type=MessageType.EVENT,
                    priority=Priority.NORMAL,
                )
                log.info("Modules recovered: %s", recovered)
            except Exception as e:
                log.error("Failed to publish recovery: %s", e)

        self._last_degraded = degraded[:]

    def _check_layers(self, status: dict):
        """Detect layer status changes (running/stopped)."""
        layers = status.get("layers", {})
        if not layers or layers == self._last_layers:
            return

        # Only report on first full status load or actual changes
        if self._last_layers:
            changes = {}
            for name, info in layers.items():
                prev = self._last_layers.get(name, {})
                curr_status = info.get("status", "unknown") if isinstance(info, dict) else str(info)
                prev_status = prev.get("status", "unknown") if isinstance(prev, dict) else str(prev)
                if curr_status != prev_status:
                    changes[name] = {"current": curr_status, "previous": prev_status}

            if changes:
                try:
                    self.client.publish(
                        "#system",
                        {
                            "event": "layer_change",
                            "changes": changes,
                        },
                        type=MessageType.DATA,
                        priority=Priority.LOW,
                    )
                    log.info("Layer changes: %s", list(changes.keys()))
                except Exception as e:
                    log.error("Failed to publish layer change: %s", e)

        self._last_layers = {k: (v.copy() if isinstance(v, dict) else v) for k, v in layers.items()}

    def _send_heartbeat(self, status: dict | None):
        """Send Genesis heartbeat to #heartbeat."""
        try:
            payload: dict[str, Any] = {
                "agent": "genesis",
                "online": self._genesis_online,
            }

            if status and self._genesis_online:
                payload["health"] = status.get("health", 0)
                payload["model"] = status.get("model", "unknown")

                # Count running layers
                layers = status.get("layers", {})
                running = sum(
                    1
                    for v in layers.values()
                    if (isinstance(v, dict) and v.get("status") == "running") or v == "running"
                )
                payload["layers_running"] = running
                payload["layers_total"] = len(layers)

                # Emotions summary
                emotions = status.get("emotions", {})
                if emotions:
                    payload["emotions"] = emotions

                # Goals summary
                goals = status.get("goals", {})
                if goals:
                    active = (
                        [
                            g
                            for g in goals.values()
                            if isinstance(g, dict) and g.get("status") == "active"
                        ]
                        if isinstance(goals, dict)
                        else []
                    )
                    payload["active_goals"] = len(active)

                # Degraded
                degraded = status.get("degraded_modules", [])
                if degraded:
                    payload["degraded_modules"] = degraded

            self.client.publish(
                "#heartbeat",
                payload,
                type=MessageType.DATA,
                priority=Priority.LOW,
            )
        except Exception:
            pass

    def _check_inbox(self):
        """Check Synapse inbox for commands directed at Genesis."""
        try:
            messages = self.client.inbox(limit=10)
        except Exception:
            return

        for msg in messages:
            payload = msg.get("payload", {})
            channel = msg.get("channel", "")
            msg_type = msg.get("type", "")

            # Commands targeted at genesis
            if channel == "#commands" and msg_type == "command":
                target = payload.get("target", "")
                action = payload.get("action", "")

                if target == "genesis":
                    self._handle_command(action, payload)

            # Queries on #learning
            elif channel == "#learning" and msg_type == "query":
                question = payload.get("question", "")
                if question:
                    self._forward_to_genesis(question, msg.get("sender", "unknown"))

        # Clear processed messages
        if messages:
            try:
                self.client.clear_inbox()
            except Exception:
                pass

    def _handle_command(self, action: str, payload: dict):
        """Handle a command directed at Genesis."""
        if action == "ask":
            question = payload.get("question", payload.get("message", ""))
            if question:
                self._forward_to_genesis(question, payload.get("sender", "synapse"))

        elif action == "status":
            # Re-publish current status
            status = self._get_json("/status")
            if status:
                try:
                    self.client.publish(
                        "#system",
                        {"event": "genesis_status_response", **status},
                        type=MessageType.RESPONSE,
                        priority=Priority.NORMAL,
                    )
                except Exception:
                    pass

    def _forward_to_genesis(self, message: str, sender: str):
        """Forward a message to Genesis /chat and publish the response."""
        if not self._genesis_online:
            log.warning("Cannot forward to Genesis — offline")
            return

        response = self._post_json(
            "/chat",
            {
                "message": message,
                "username": sender,
                "display_name": f"via-synapse-{sender}",
            },
        )

        if response:
            try:
                self.client.publish(
                    "#learning",
                    {
                        "event": "genesis_response",
                        "question": message,
                        "response": response.get("response", ""),
                        "emotions": response.get("emotions", {}),
                        "asked_by": sender,
                    },
                    type=MessageType.RESPONSE,
                    priority=Priority.NORMAL,
                )
                log.info("Forwarded to Genesis and published response")
            except Exception as e:
                log.error("Failed to publish Genesis response: %s", e)


# Singleton
_bridge: GenesisBridge | None = None


def get_bridge() -> GenesisBridge:
    global _bridge
    if _bridge is None:
        _bridge = GenesisBridge()
    return _bridge


def start_bridge() -> bool:
    return get_bridge().start()


def stop_bridge():
    if _bridge:
        _bridge.stop()
