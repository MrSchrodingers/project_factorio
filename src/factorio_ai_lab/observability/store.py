from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Self
from uuid import uuid4


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class RunMetadata:
    environment: str
    seed: int
    git_sha: str
    model: str | None = None
    config: Mapping[str, Any] | None = None


class ExperimentStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA foreign_keys=ON")
        self._create_schema()

    def _create_schema(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY,
                started_at TEXT NOT NULL,
                ended_at TEXT,
                status TEXT NOT NULL,
                environment TEXT NOT NULL,
                seed INTEGER NOT NULL,
                git_sha TEXT NOT NULL,
                model TEXT,
                config_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
                step INTEGER NOT NULL,
                name TEXT NOT NULL,
                value REAL NOT NULL,
                recorded_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_metrics_run_name
            ON metrics(run_id, name, step);

            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
                step INTEGER NOT NULL,
                kind TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                recorded_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_events_run_kind
            ON events(run_id, kind, step);
            """
        )
        self.connection.commit()

    def start_run(self, metadata: RunMetadata, run_id: str | None = None) -> str:
        run_id = run_id or uuid4().hex
        config = {} if metadata.config is None else dict(metadata.config)
        self.connection.execute(
            """
            INSERT INTO runs(
                run_id, started_at, status, environment, seed, git_sha, model, config_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                _utc_now(),
                "running",
                metadata.environment,
                metadata.seed,
                metadata.git_sha,
                metadata.model,
                json.dumps(config, sort_keys=True, separators=(",", ":")),
            ),
        )
        self.connection.commit()
        return run_id

    def log_metric(self, run_id: str, step: int, name: str, value: float) -> None:
        self.connection.execute(
            """
            INSERT INTO metrics(run_id, step, name, value, recorded_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (run_id, step, name, float(value), _utc_now()),
        )
        self.connection.commit()

    def log_event(
        self,
        run_id: str,
        step: int,
        kind: str,
        payload: Mapping[str, Any] | None = None,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO events(run_id, step, kind, payload_json, recorded_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                run_id,
                step,
                kind,
                json.dumps({} if payload is None else dict(payload), sort_keys=True),
                _utc_now(),
            ),
        )
        self.connection.commit()

    def finish_run(self, run_id: str, status: str = "completed") -> None:
        self.connection.execute(
            "UPDATE runs SET status = ?, ended_at = ? WHERE run_id = ?",
            (status, _utc_now(), run_id),
        )
        self.connection.commit()

    def metrics_for_run(self, run_id: str) -> list[tuple[int, str, float]]:
        cursor = self.connection.execute(
            "SELECT step, name, value FROM metrics WHERE run_id = ? ORDER BY id",
            (run_id,),
        )
        return [(int(step), str(name), float(value)) for step, name, value in cursor.fetchall()]

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()
