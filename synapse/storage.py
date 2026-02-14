"""SQLite persistence layer for Synapse messages."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from synapse.models import MessageType, Priority, SynapseMessage


class MessageStore:
    """Persists messages to SQLite for durability and replay."""

    def __init__(self, db_path: str | Path = "~/.synapse/messages.db"):
        self.db_path = Path(db_path).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    channel TEXT NOT NULL,
                    sender TEXT NOT NULL,
                    type TEXT NOT NULL DEFAULT 'event',
                    priority INTEGER NOT NULL DEFAULT 2,
                    payload TEXT NOT NULL DEFAULT '{}',
                    timestamp TEXT NOT NULL,
                    ttl INTEGER DEFAULT 0,
                    reply_to TEXT
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_msg_channel ON messages(channel)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_msg_ts ON messages(timestamp DESC)")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_msg_channel_ts ON messages(channel, timestamp DESC)"
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_msg_sender ON messages(sender)")
            conn.commit()

    def store(self, msg: SynapseMessage) -> bool:
        """Store a message. Returns True on success, False on duplicate."""
        try:
            with self._conn() as conn:
                conn.execute(
                    """INSERT OR IGNORE INTO messages
                       (id, channel, sender, type, priority, payload, timestamp, ttl, reply_to)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        msg.id,
                        msg.channel,
                        msg.sender,
                        msg.type.value,
                        msg.priority.value,
                        json.dumps(msg.payload),
                        msg.timestamp.isoformat(),
                        msg.ttl,
                        msg.reply_to,
                    ),
                )
                conn.commit()
                return True
        except sqlite3.Error:
            return False

    def get_history(
        self,
        channel: str,
        limit: int = 50,
        since: datetime | None = None,
    ) -> list[SynapseMessage]:
        """Get messages for a channel, newest first."""
        params: list = [channel]
        since_clause = ""
        if since:
            since_clause = "AND timestamp > ?"
            params.append(since.isoformat())
        params.append(limit)

        with self._conn() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                f"""SELECT * FROM messages
                    WHERE channel = ? {since_clause}
                    ORDER BY timestamp DESC
                    LIMIT ?""",
                params,
            ).fetchall()
            return [_row_to_msg(r) for r in reversed(rows)]

    def get_by_sender(self, sender: str, limit: int = 50) -> list[SynapseMessage]:
        with self._conn() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM messages WHERE sender = ? ORDER BY timestamp DESC LIMIT ?",
                (sender, limit),
            ).fetchall()
            return [_row_to_msg(r) for r in reversed(rows)]

    def count(self, channel: str | None = None) -> int:
        with self._conn() as conn:
            if channel:
                row = conn.execute(
                    "SELECT COUNT(*) FROM messages WHERE channel = ?", (channel,)
                ).fetchone()
            else:
                row = conn.execute("SELECT COUNT(*) FROM messages").fetchone()
            return row[0]

    def cleanup_expired(self) -> int:
        """Delete messages whose TTL has expired. Returns count deleted."""
        with self._conn() as conn:
            cur = conn.execute(
                """DELETE FROM messages
                   WHERE ttl > 0
                   AND CAST(
                       (julianday('now') - julianday(timestamp)) * 86400
                       AS INTEGER
                   ) > ttl"""
            )
            conn.commit()
            return cur.rowcount

    def stats(self) -> dict:
        with self._conn() as conn:
            total = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
            by_channel = conn.execute(
                "SELECT channel, COUNT(*) as cnt FROM messages GROUP BY channel"
            ).fetchall()
            db_size = self.db_path.stat().st_size / (1024 * 1024) if self.db_path.exists() else 0
        return {
            "total_messages": total,
            "by_channel": {r[0]: r[1] for r in by_channel},
            "db_size_mb": round(db_size, 2),
        }


def _row_to_msg(row: sqlite3.Row) -> SynapseMessage:
    return SynapseMessage(
        id=row["id"],
        channel=row["channel"],
        sender=row["sender"],
        type=MessageType(row["type"]),
        priority=Priority(row["priority"]),
        payload=json.loads(row["payload"]),
        timestamp=datetime.fromisoformat(row["timestamp"]),
        ttl=row["ttl"],
        reply_to=row["reply_to"],
    )
