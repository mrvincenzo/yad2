"""
store.py — SQLite-backed store for tracking seen listing tokens.

Prevents re-alerting on listings already notified about.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


class SeenStore:
    """
    Persistent store of listing tokens that have already been sent as alerts.

    Thread-safe for single-process use (SQLite WAL mode).
    """

    def __init__(self, db_path: str | Path) -> None:
        self._path = Path(db_path).expanduser()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS seen_tokens (
                token           TEXT PRIMARY KEY,
                neighborhood_id INTEGER NOT NULL,
                price           INTEGER,
                first_seen_at   TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
                notified_at     TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
            );

            CREATE TABLE IF NOT EXISTS price_history (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                token   TEXT NOT NULL,
                price   INTEGER NOT NULL,
                seen_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
                FOREIGN KEY(token) REFERENCES seen_tokens(token)
            );

            CREATE TABLE IF NOT EXISTS run_log (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at          TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
                neighborhood_id INTEGER NOT NULL,
                fetched_count   INTEGER NOT NULL DEFAULT 0,
                new_count       INTEGER NOT NULL DEFAULT 0,
                error           TEXT
            );
            """
        )
        self._conn.execute(
            """
            INSERT INTO price_history (token, price, seen_at)
            SELECT token, price, first_seen_at FROM seen_tokens
            WHERE price IS NOT NULL AND token NOT IN (SELECT DISTINCT token FROM price_history)
            """
        )
        self._conn.commit()

    def get_price_history(self, token: str) -> list[int] | None:
        """Return history of prices for this token. Returns None if never seen."""
        row = self._conn.execute("SELECT 1 FROM seen_tokens WHERE token = ?", (token,)).fetchone()
        if row is None:
            return None

        rows = self._conn.execute(
            "SELECT price FROM price_history WHERE token = ? ORDER BY id ASC", (token,)
        ).fetchall()
        return [r[0] for r in rows]

    def mark_seen(self, token: str, neighborhood_id: int, price: int) -> None:
        """Record a token and its price as notified."""
        self._conn.execute(
            """
            INSERT INTO seen_tokens (token, neighborhood_id, price)
            VALUES (?, ?, ?)
            ON CONFLICT(token) DO UPDATE SET
                price = excluded.price,
                notified_at = datetime('now', 'localtime')
            """,
            (token, neighborhood_id, price),
        )

        latest = self._conn.execute(
            "SELECT price FROM price_history WHERE token = ? ORDER BY id DESC LIMIT 1", (token,)
        ).fetchone()
        if not latest or latest[0] != price:
            self._conn.execute(
                "INSERT INTO price_history (token, price) VALUES (?, ?)", (token, price)
            )

        self._conn.commit()

    def log_run(
        self,
        neighborhood_id: int,
        fetched_count: int,
        new_count: int,
        error: str | None = None,
    ) -> None:
        """Record a watcher run for diagnostics."""
        self._conn.execute(
            """
            INSERT INTO run_log (neighborhood_id, fetched_count, new_count, error)
            VALUES (?, ?, ?, ?)
            """,
            (neighborhood_id, fetched_count, new_count, error),
        )
        self._conn.commit()

    def stats(self) -> dict:
        """Return basic stats for the status command."""
        total = self._conn.execute("SELECT COUNT(*) FROM seen_tokens").fetchone()[0]
        runs = self._conn.execute("SELECT COUNT(*) FROM run_log").fetchone()[0]
        last_run = self._conn.execute(
            "SELECT run_at, neighborhood_id, fetched_count, new_count, error "
            "FROM run_log ORDER BY id DESC LIMIT 10"
        ).fetchall()
        return {
            "total_seen": total,
            "total_runs": runs,
            "recent_runs": [
                {
                    "run_at": r[0],
                    "neighborhood_id": r[1],
                    "fetched": r[2],
                    "new": r[3],
                    "error": r[4],
                }
                for r in last_run
            ],
        }

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> SeenStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
