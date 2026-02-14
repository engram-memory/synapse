"""
TESS → Synapse Monitor — Non-invasive log-tailing bridge.

Watches the TESS V12 log file for trade events and publishes them
to the Synapse message bus. Zero modifications to TESS code.

Events captured:
- SWARM decision → #market-data (NORMAL)
- Trade OPENED → #market-data (HIGH) + #alerts if high conviction
- Trade CLOSED (SL/TP/LIQUIDATION) → #market-data (HIGH)
- Volatility regime → #market-data (NORMAL)
- Cycle start → #market-data (LOW)
- Heartbeat (capital, log size) → #heartbeat (LOW)
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from pathlib import Path

from synapse.client import SynapseClient
from synapse.models import MessageType, Priority

log = logging.getLogger("tess_monitor")

# --- Config ---
TESS_LOG = Path(os.getenv("TESS_LOG", "/home/levent/tess_trader/v11/logs/v12.log"))
TESS_PERF = Path(os.getenv("TESS_PERF", "/home/levent/tess_trader/v11/state/trade_history.json"))
SYNAPSE_URL = os.getenv("SYNAPSE_URL", "http://localhost:8200")
POLL_INTERVAL = 3  # seconds between log checks

# --- V12 Log line patterns ---
RE_SWARM = re.compile(r"SWARM: (\w+) \(conf=([0-9.]+)%, consensus=([0-9.]+)%\)")
RE_OPENED = re.compile(r"OPENED (\w+) (\w+)")
RE_ENTRY_DETAIL = re.compile(r"Entry: \$([0-9.]+)")
RE_SL_TP = re.compile(r"SL: \$([0-9.]+) \| TP: \$([0-9.]+)")
RE_CLOSED = re.compile(r"CLOSED (\w+) (\w+) - (\w+)")
RE_PNL = re.compile(r"PnL: \$([0-9.+-]+)")
RE_CAPITAL = re.compile(r"Capital: \$([0-9.]+)")
RE_VOLATILITY = re.compile(r"Volatility: (\w+) \(conf=([0-9.]+)%, size_mult=([0-9.]+)\)")
RE_CYCLE = re.compile(r"CYCLE (\d+) - (.+)")
RE_SKIP = re.compile(r"SKIP - (.+)")
RE_VETO = re.compile(r"VETOED: (.+)")


class TESSMonitor:
    """Watches TESS log file and publishes events to Synapse."""

    def __init__(
        self,
        log_path: Path = TESS_LOG,
        synapse_url: str = SYNAPSE_URL,
        poll_interval: int = POLL_INTERVAL,
    ):
        self.log_path = log_path
        self.poll_interval = poll_interval
        self.client = SynapseClient("tess", synapse_url)
        self._running = False
        self._thread: threading.Thread | None = None
        self._file_pos = 0
        self._last_signal: dict | None = None
        self._last_regime: str = ""
        self._last_open: dict | None = None
        self._last_close: dict | None = None
        self._current_symbol: str | None = None

    def start(self) -> bool:
        """Start the monitor as a background thread."""
        if self._running:
            return False

        if not self.log_path.exists():
            log.error("TESS log not found: %s", self.log_path)
            return False

        # Register TESS on the bus
        try:
            self.client.register(
                capabilities=["trading", "analysis", "signals"],
                channels=["#alerts", "#market-data", "#heartbeat"],
            )
        except Exception as e:
            log.error("Cannot register on Synapse: %s", e)
            return False

        # Seek to end of file (only process new entries)
        self._file_pos = self.log_path.stat().st_size

        self._running = True
        self._thread = threading.Thread(target=self._tail_loop, daemon=True, name="tess-monitor")
        self._thread.start()
        log.info("TESS monitor started — tailing %s", self.log_path)
        return True

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=10)
        log.info("TESS monitor stopped")

    def _tail_loop(self):
        """Main loop: tail the log file for new lines."""
        heartbeat_counter = 0

        while self._running:
            try:
                new_lines = self._read_new_lines()
                for line in new_lines:
                    self._process_line(line)

                # Heartbeat every ~60s (20 cycles × 3s)
                heartbeat_counter += 1
                if heartbeat_counter >= 20:
                    self._send_heartbeat()
                    heartbeat_counter = 0

                # Keep TESS alive on the bus
                try:
                    self.client.heartbeat()
                except Exception:
                    pass

            except Exception as e:
                log.error("Monitor error: %s", e)

            time.sleep(self.poll_interval)

    def _read_new_lines(self) -> list[str]:
        """Read new lines from the log file since last position."""
        try:
            current_size = self.log_path.stat().st_size

            # Log was truncated/rotated
            if current_size < self._file_pos:
                log.info("Log rotated, resetting position")
                self._file_pos = 0

            if current_size == self._file_pos:
                return []

            with open(self.log_path, encoding="utf-8", errors="replace") as f:
                f.seek(self._file_pos)
                lines = f.readlines()
                self._file_pos = f.tell()
                return [line.strip() for line in lines if line.strip()]

        except Exception as e:
            log.error("Error reading log: %s", e)
            return []

    def _process_line(self, line: str):
        """Parse a V12 log line and publish relevant events."""

        # Cycle start
        m = RE_CYCLE.search(line)
        if m:
            cycle_num, timestamp = m.groups()
            self._publish_cycle(int(cycle_num), timestamp.strip())
            return

        # SWARM decision (= signal)
        m = RE_SWARM.search(line)
        if m:
            action, confidence, consensus = m.groups()
            self._last_signal = {
                "action": action,
                "confidence": float(confidence),
                "consensus": float(consensus),
                "symbol": self._current_symbol or "UNKNOWN",
            }
            self._publish_signal()
            return

        # Trade opened
        m = RE_OPENED.search(line)
        if m:
            direction, symbol = m.groups()
            self._current_symbol = symbol
            self._last_open = {"direction": direction, "symbol": symbol}
            self._publish_trade_opened(direction, symbol)
            return

        # Entry details (comes on line after OPENED)
        m = RE_ENTRY_DETAIL.search(line)
        if m and self._last_open:
            self._last_open["entry"] = float(m.group(1))
            return

        # SL/TP details
        m = RE_SL_TP.search(line)
        if m and self._last_open:
            self._last_open["stop_loss"] = float(m.group(1))
            self._last_open["take_profit"] = float(m.group(2))
            return

        # Trade closed
        m = RE_CLOSED.search(line)
        if m:
            direction, symbol, reason = m.groups()
            self._last_close = {"direction": direction, "symbol": symbol, "reason": reason}
            return

        # PnL line (comes after CLOSED)
        m = RE_PNL.search(line)
        if m and self._last_close:
            pnl = float(m.group(1))
            self._publish_trade_closed(
                self._last_close["symbol"],
                self._last_close["reason"],
                pnl,
            )
            self._last_close = None
            return

        # Volatility regime
        m = RE_VOLATILITY.search(line)
        if m:
            vol_regime, vol_conf, size_mult = m.groups()
            regime_key = f"{vol_regime}|{vol_conf}"
            if regime_key != self._last_regime:
                self._last_regime = regime_key
                self._publish_volatility(vol_regime, float(vol_conf), float(size_mult))
            return

        # Analyzing symbol (track current symbol for SWARM)
        if "Analyzing " in line and "..." in line:
            sym = line.split("Analyzing ")[-1].replace("...", "").strip()
            if sym:
                self._current_symbol = sym
            return

        # Skip / Veto (publish as info)
        m = RE_SKIP.search(line)
        if m:
            self._publish_skip(self._current_symbol or "UNKNOWN", m.group(1))
            return

    def _publish_signal(self):
        """Publish a SWARM decision to #market-data."""
        if not self._last_signal:
            return

        sig = self._last_signal
        try:
            self.client.publish(
                "#market-data",
                {
                    "event": "swarm_decision",
                    **sig,
                },
                type=MessageType.DATA,
                priority=Priority.NORMAL,
            )
            log.info(
                "Published swarm: %s %s (conf=%.1f%%, consensus=%.1f%%)",
                sig["action"],
                sig["symbol"],
                sig["confidence"],
                sig["consensus"],
            )
        except Exception as e:
            log.error("Failed to publish signal: %s", e)

    def _publish_trade_opened(self, direction: str, symbol: str):
        """Publish trade opened to #market-data and optionally #alerts."""
        payload = {
            "event": "trade_opened",
            "direction": direction,
            "symbol": symbol,
        }

        # Include SWARM data if available
        if self._last_signal and self._last_signal.get("symbol") == symbol:
            payload["confidence"] = self._last_signal.get("confidence", 0)
            payload["consensus"] = self._last_signal.get("consensus", 0)

        try:
            self.client.publish(
                "#market-data",
                payload,
                type=MessageType.EVENT,
                priority=Priority.HIGH,
            )

            # High confidence trades also go to #alerts (→ Telegram!)
            confidence = payload.get("confidence", 0)
            if confidence >= 80:
                self.client.alert(
                    f"TESS {direction} {symbol} opened! Confidence={confidence:.1f}%",
                )

            log.info("Published trade opened: %s %s", direction, symbol)
        except Exception as e:
            log.error("Failed to publish trade: %s", e)

        self._last_signal = None

    def _publish_trade_closed(self, symbol: str, reason: str, pnl: float):
        """Publish trade closed to #market-data, losses to #alerts."""
        payload = {
            "event": "trade_closed",
            "symbol": symbol,
            "reason": reason,
            "pnl_usd": pnl,
        }

        try:
            self.client.publish(
                "#market-data",
                payload,
                type=MessageType.EVENT,
                priority=Priority.HIGH,
            )

            # Large losses go to #alerts as CRITICAL (→ Telegram!)
            if pnl < -100:
                self.client.alert(
                    f"TESS {symbol} closed with ${pnl:.2f} loss! Reason: {reason}",
                )

            log.info("Published trade closed: %s %s P&L=$%.2f", symbol, reason, pnl)
        except Exception as e:
            log.error("Failed to publish trade close: %s", e)

    def _publish_volatility(self, regime: str, confidence: float, size_mult: float):
        """Publish volatility regime to #market-data."""
        try:
            self.client.publish(
                "#market-data",
                {
                    "event": "volatility_regime",
                    "symbol": self._current_symbol or "UNKNOWN",
                    "regime": regime,
                    "confidence_pct": confidence,
                    "size_multiplier": size_mult,
                },
                type=MessageType.DATA,
                priority=Priority.NORMAL,
            )
            log.info(
                "Published volatility: %s %s (conf=%.1f%%)",
                self._current_symbol,
                regime,
                confidence,
            )
        except Exception as e:
            log.error("Failed to publish volatility: %s", e)

    def _publish_cycle(self, cycle_num: int, timestamp: str):
        """Publish cycle start to #market-data."""
        try:
            self.client.publish(
                "#market-data",
                {
                    "event": "cycle_start",
                    "cycle": cycle_num,
                    "timestamp": timestamp,
                },
                type=MessageType.EVENT,
                priority=Priority.LOW,
            )
            log.info("Published cycle start: #%d", cycle_num)
        except Exception as e:
            log.error("Failed to publish cycle: %s", e)

    def _publish_skip(self, symbol: str, reason: str):
        """Publish skip event to #market-data."""
        try:
            self.client.publish(
                "#market-data",
                {
                    "event": "symbol_skipped",
                    "symbol": symbol,
                    "reason": reason,
                },
                type=MessageType.DATA,
                priority=Priority.LOW,
            )
        except Exception as e:
            log.error("Failed to publish skip: %s", e)

    def _send_heartbeat(self):
        """Send TESS heartbeat with basic status."""
        try:
            payload = {
                "agent": "tess",
                "status": "monitoring",
                "log_file": str(self.log_path),
                "log_size_kb": round(self.log_path.stat().st_size / 1024, 1),
            }

            # Try to read perf_store for equity data
            try:
                if TESS_PERF.exists() and TESS_PERF.stat().st_size > 10:
                    with open(TESS_PERF) as f:
                        perf = json.load(f)
                    # Count total trades across all symbols
                    total_trades = sum(len(v) for v in perf.values() if isinstance(v, list))
                    payload["total_trades_recorded"] = total_trades
            except Exception:
                pass

            self.client.publish(
                "#heartbeat",
                payload,
                type=MessageType.DATA,
                priority=Priority.LOW,
            )
        except Exception:
            pass


# Singleton
_monitor: TESSMonitor | None = None


def get_monitor() -> TESSMonitor:
    global _monitor
    if _monitor is None:
        _monitor = TESSMonitor()
    return _monitor


def start_monitor() -> bool:
    return get_monitor().start()


def stop_monitor():
    if _monitor:
        _monitor.stop()
