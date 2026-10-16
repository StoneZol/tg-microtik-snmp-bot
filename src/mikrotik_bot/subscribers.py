from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from mikrotik_bot.whitelist import WhitelistACL, normalize_username


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Subscriber:
    chat_id: str
    user_id: int | None
    username: str | None


class SubscriberStore:
    """Tracks chats that have talked to each bot token (SQLite)."""

    def __init__(self, db_path: str | Path) -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS bot_subscribers (
                    token_hash TEXT NOT NULL,
                    chat_id TEXT NOT NULL,
                    user_id INTEGER,
                    username TEXT,
                    first_seen TEXT NOT NULL,
                    last_seen TEXT NOT NULL,
                    PRIMARY KEY (token_hash, chat_id)
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_bot_subscribers_token
                ON bot_subscribers (token_hash)
                """
            )

    def touch(
        self,
        token: str,
        chat_id: str,
        user_id: int | None = None,
        username: str | None = None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        token_hash = _token_hash(token)
        stored_username = normalize_username(username)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO bot_subscribers (
                    token_hash, chat_id, user_id, username, first_seen, last_seen
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(token_hash, chat_id) DO UPDATE SET
                    user_id = COALESCE(excluded.user_id, bot_subscribers.user_id),
                    username = COALESCE(excluded.username, bot_subscribers.username),
                    last_seen = excluded.last_seen
                """,
                (token_hash, str(chat_id), user_id, stored_username, now, now),
            )

    def list_subscribers(self, token: str) -> list[Subscriber]:
        token_hash = _token_hash(token)
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT chat_id, user_id, username
                FROM bot_subscribers
                WHERE token_hash = ?
                ORDER BY chat_id
                """,
                (token_hash,),
            ).fetchall()
        return [
            Subscriber(
                chat_id=str(row["chat_id"]),
                user_id=int(row["user_id"]) if row["user_id"] is not None else None,
                username=row["username"],
            )
            for row in rows
        ]

    def recipients(self, token: str, whitelist: WhitelistACL | None) -> list[str]:
        """
        Chat ids that should receive pushes for this bot entry.

        whitelist is None → everyone who activated the bot.
        whitelist is ACL → only matching user ids / usernames (who have activated).
        """
        subs = self.list_subscribers(token)
        if whitelist is None:
            return [s.chat_id for s in subs]

        out: list[str] = []
        for sub in subs:
            if whitelist.allows(sub.user_id, sub.username):
                out.append(sub.chat_id)
            elif sub.user_id is None and sub.chat_id.lstrip("-").isdigit():
                # private chats: chat_id == user_id
                if whitelist.allows(int(sub.chat_id), sub.username):
                    out.append(sub.chat_id)

        seen: set[str] = set()
        unique: list[str] = []
        for chat_id in out:
            if chat_id not in seen:
                seen.add(chat_id)
                unique.append(chat_id)
        return unique
