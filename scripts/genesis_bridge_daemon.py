#!/usr/bin/env python3
"""Standalone Genesis → Synapse bridge daemon.

Polls Genesis API on :7777 and publishes state changes
to the Synapse message bus. Non-invasive — reads only.
"""

import logging
import signal
import sys
import time

sys.path.insert(0, "/home/levent/synapse")

from synapse.genesis_bridge import GenesisBridge  # noqa: E402 — must be after sys.path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [genesis-bridge] %(levelname)s: %(message)s",
)
log = logging.getLogger("genesis-bridge-daemon")

bridge = GenesisBridge(poll_interval=10)


def shutdown(signum, frame):
    log.info("Shutting down (signal %d)", signum)
    bridge.stop()
    sys.exit(0)


signal.signal(signal.SIGTERM, shutdown)
signal.signal(signal.SIGINT, shutdown)

if __name__ == "__main__":
    log.info("Starting Genesis → Synapse bridge daemon")

    if not bridge.start():
        log.error("Failed to start — is Synapse running?")
        sys.exit(1)

    log.info("Bridge active. Polling Genesis API for state changes.")

    while True:
        time.sleep(60)
