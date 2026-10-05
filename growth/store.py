"""The engine's memory: one SQLite file with the run ledger, AI usage, side effects and blocks.

Every write that decides "does this happen?" runs inside BEGIN IMMEDIATE, so two processes can
never both claim the same slot or the same side effect.
"""

from __future__ import annotations

import json
import os
import socket
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from .util import iso, utcnow

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY,
  scope TEXT NOT NULL,
  job TEXT NOT NULL,
  slot TEXT NOT NULL,
  status TEXT NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 1,
  pid INTEGER,
  host TEXT,
  started_at TEXT,
  finished_at TEXT,
  summary TEXT NOT NULL DEFAULT '',
  UNIQUE (scope, job, slot)
);
CREATE TABLE IF NOT EXISTS ai_runs (
  id INTEGER PRIMARY KEY,
  scope TEXT NOT NULL,
  job TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  ok INTEGER NOT NULL DEFAULT 0,
  limited INTEGER NOT NULL DEFAULT 0,
  detail TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS effects (
  scope TEXT NOT NULL,
  key TEXT NOT NULL,
  channel TEXT NOT NULL,
  kind TEXT NOT NULL,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  finished_at TEXT,
  detail TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (scope, key)
);
CREATE TABLE IF NOT EXISTS blocked (
  id INTEGER PRIMARY KEY,
  scope TEXT NOT NULL,
  job TEXT NOT NULL,
  rule TEXT NOT NULL,
  message TEXT NOT NULL,
  at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS texts (
  fingerprint TEXT NOT NULL,
  community TEXT NOT NULL,
  scope TEXT NOT NULL,
  channel TEXT NOT NULL,
  at TEXT NOT NULL,
  PRIMARY KEY (fingerprint, community)
);
CREATE TABLE IF NOT EXISTS kv (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
"""

# A run's status. "running" rows found by a tick that holds the engine lock are stale (their process
# died), because only the lock holder runs jobs.
RUNNING, OK, FAILED, INTERRUPTED, MISSED = "running", "ok", "failed", "interrupted", "missed"


@dataclass(frozen=True)
class Run:
    scope: str
    job: str
    slot: str
    status: str
    attempts: int
    started_at: str | None
    finished_at: str | None
    summary: str


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.db = sqlite3.connect(path, isolation_level=None, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.close()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield self.db
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        self.db.execute("COMMIT")

    # ---- run ledger -------------------------------------------------------------------------

    def claim(self, scope: str, job: str, slot: str, *, retry: tuple[str, ...] = (), max_attempts: int = 1) -> bool:
        """Mark (scope, job, slot) as running. True only for the one caller that may run it.

        A slot with no row is claimed. A slot whose row is in `retry` (e.g. failed) and has attempts
        left is claimed again. Anything else, above all ok or running, is never claimed twice.
        """
        now = iso(utcnow())
        with self.tx() as db:
            row = db.execute(
                "SELECT status, attempts FROM runs WHERE scope=? AND job=? AND slot=?", (scope, job, slot)
            ).fetchone()
            if row is None:
                db.execute(
                    "INSERT INTO runs (scope, job, slot, status, attempts, pid, host, started_at) "
                    "VALUES (?, ?, ?, ?, 1, ?, ?, ?)",
                    (scope, job, slot, RUNNING, os.getpid(), socket.gethostname(), now),
                )
                return True
            if row["status"] in retry and row["attempts"] < max_attempts:
                db.execute(
                    "UPDATE runs SET status=?, attempts=attempts+1, pid=?, host=?, started_at=?, finished_at=NULL "
                    "WHERE scope=? AND job=? AND slot=?",
                    (RUNNING, os.getpid(), socket.gethostname(), now, scope, job, slot),
                )
                return True
            return False

    def finish(self, scope: str, job: str, slot: str, status: str, summary: str) -> None:
        self.db.execute(
            "UPDATE runs SET status=?, summary=?, finished_at=? WHERE scope=? AND job=? AND slot=?",
            (status, summary[:4000], iso(utcnow()), scope, job, slot),
        )

    def mark_missed(self, scope: str, job: str, slot: str, reason: str) -> bool:
        """Record a slot that will never run (too late, or no AI budget). False if it already had a row."""
        cur = self.db.execute(
            "INSERT OR IGNORE INTO runs (scope, job, slot, status, attempts, started_at, finished_at, summary) "
            "VALUES (?, ?, ?, ?, 0, NULL, ?, ?)",
            (scope, job, slot, MISSED, iso(utcnow()), reason),
        )
        return cur.rowcount == 1

    def mark_stale_running(self) -> list[Run]:
        """Turn every leftover "running" row into "interrupted". Call only while holding the engine lock."""
        with self.tx() as db:
            rows = db.execute("SELECT * FROM runs WHERE status=?", (RUNNING,)).fetchall()
            db.execute(
                "UPDATE runs SET status=?, finished_at=?, summary='process ended before the job finished' "
                "WHERE status=?",
                (INTERRUPTED, iso(utcnow()), RUNNING),
            )
        return [self._run(r) for r in rows]

    def run(self, scope: str, job: str, slot: str) -> Run | None:
        row = self.db.execute("SELECT * FROM runs WHERE scope=? AND job=? AND slot=?", (scope, job, slot)).fetchone()
        return self._run(row) if row else None

    def last_slot(self, scope: str, job: str) -> str | None:
        row = self.db.execute(
            "SELECT MAX(slot) AS s FROM runs WHERE scope=? AND job=? AND slot NOT LIKE 'manual:%'", (scope, job)
        ).fetchone()
        return row["s"] if row and row["s"] else None

    def runs_since(self, since: datetime, scope: str | None = None) -> list[Run]:
        query = "SELECT * FROM runs WHERE COALESCE(finished_at, started_at) >= ?"
        args: list[Any] = [iso(since)]
        if scope is not None:
            query += " AND scope = ?"
            args.append(scope)
        return [self._run(r) for r in self.db.execute(query + " ORDER BY COALESCE(started_at, finished_at)", args).fetchall()]

    @staticmethod
    def _run(row: sqlite3.Row) -> Run:
        return Run(
            scope=row["scope"],
            job=row["job"],
            slot=row["slot"],
            status=row["status"],
            attempts=row["attempts"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            summary=row["summary"],
        )

    # ---- AI usage ---------------------------------------------------------------------------

    def ai_started(self, scope: str, job: str) -> int:
        cur = self.db.execute(
            "INSERT INTO ai_runs (scope, job, started_at) VALUES (?, ?, ?)", (scope, job, iso(utcnow()))
        )
        return int(cur.lastrowid or 0)

    def ai_finished(self, row_id: int, *, ok: bool, limited: bool, detail: str) -> None:
        self.db.execute(
            "UPDATE ai_runs SET finished_at=?, ok=?, limited=?, detail=? WHERE id=?",
            (iso(utcnow()), int(ok), int(limited), detail[:2000], row_id),
        )

    def ai_runs_since(self, since: datetime, scope: str | None = None) -> int:
        query = "SELECT COUNT(*) AS n FROM ai_runs WHERE started_at >= ?"
        args: list[Any] = [iso(since)]
        if scope is not None:
            query += " AND scope = ?"
            args.append(scope)
        return int(self.db.execute(query, args).fetchone()["n"])

    # ---- side effects (exactly-once at most) --------------------------------------------------

    def effect_begin(self, scope: str, key: str, channel: str, kind: str, *, retry_failed: bool) -> str:
        """Reserve a side effect before doing it. Returns "go", "done", "pending" or "failed".

        "pending" means an earlier attempt started and never reported back (the PC went off), so
        nobody knows whether it happened; the engine never repeats it.
        """
        now = iso(utcnow())
        with self.tx() as db:
            row = db.execute("SELECT status FROM effects WHERE scope=? AND key=?", (scope, key)).fetchone()
            if row is None:
                db.execute(
                    "INSERT INTO effects (scope, key, channel, kind, status, created_at) VALUES (?, ?, ?, ?, 'pending', ?)",
                    (scope, key, channel, kind, now),
                )
                return "go"
            if row["status"] == "failed" and retry_failed:
                db.execute(
                    "UPDATE effects SET status='pending', created_at=?, finished_at=NULL WHERE scope=? AND key=?",
                    (now, scope, key),
                )
                return "go"
            return str(row["status"])

    def effect_end(self, scope: str, key: str, status: str, detail: str = "") -> None:
        self.db.execute(
            "UPDATE effects SET status=?, finished_at=?, detail=? WHERE scope=? AND key=?",
            (status, iso(utcnow()), detail[:2000], scope, key),
        )

    def effects_since(self, since: datetime, scope: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM effects WHERE created_at >= ?"
        args: list[Any] = [iso(since)]
        if scope is not None:
            query += " AND scope = ?"
            args.append(scope)
        return [dict(r) for r in self.db.execute(query, args).fetchall()]

    # ---- policy blocks and text fingerprints ---------------------------------------------------

    def record_block(self, scope: str, job: str, rule: str, message: str) -> None:
        self.db.execute(
            "INSERT INTO blocked (scope, job, rule, message, at) VALUES (?, ?, ?, ?, ?)",
            (scope, job, rule, message[:2000], iso(utcnow())),
        )

    def blocks_since(self, since: datetime, scope: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT scope, job, rule, message, at FROM blocked WHERE at >= ?"
        args: list[Any] = [iso(since)]
        if scope is not None:
            query += " AND scope = ?"
            args.append(scope)
        return [dict(r) for r in self.db.execute(query + " ORDER BY at", args).fetchall()]

    def text_communities(self, fingerprint: str) -> list[str]:
        rows = self.db.execute("SELECT community FROM texts WHERE fingerprint=?", (fingerprint,)).fetchall()
        return [r["community"] for r in rows]

    def record_text(self, fingerprint: str, community: str, scope: str, channel: str) -> None:
        self.db.execute(
            "INSERT OR IGNORE INTO texts (fingerprint, community, scope, channel, at) VALUES (?, ?, ?, ?, ?)",
            (fingerprint, community, scope, channel, iso(utcnow())),
        )

    # ---- small key-value memory ----------------------------------------------------------------

    def get(self, key: str, default: Any = None) -> Any:
        row = self.db.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def put(self, key: str, value: Any) -> None:
        self.db.execute(
            "INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value)),
        )
