#!/usr/bin/env python3
"""Standalone TESS → Synapse monitor daemon.

Tails the TESS V9 log file and publishes trading events
to the Synapse message bus. Non-invasive — reads only.
"""

import logging
import signal
import sys
import time

sys.path.insert(0, "/home/levent/synapse")

from synapse.tess_monitor import TESSMonitor  # noqa: E402 — must be after sys.path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [tess-monitor] %(levelname)s: %(message)s",
)
log = logging.getLogger("tess-monitor-daemon")

monitor = TESSMonitor(poll_interval=3)


def shutdown(signum, frame):
    log.info("Shutting down (signal %d)", signum)
    monitor.stop()
    sys.exit(0)


signal.signal(signal.SIGTERM, shutdown)
signal.signal(signal.SIGINT, shutdown)

if __name__ == "__main__":
    log.info("Starting TESS → Synapse monitor daemon")

    if not monitor.start():
        log.error("Failed to start — is TESS log present and Synapse running?")
        sys.exit(1)

    log.info("Monitor active. Tailing TESS log for trade events.")

    while True:
        time.sleep(60)
