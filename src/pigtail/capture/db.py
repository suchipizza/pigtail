"""Postgres persistence for capture records v0 (M1-T1). Upserts are idempotent by id."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from pigtail.capture.models import Case, Evidence, Repo, Run


class CaptureDB:
    """Thin wrapper over an autocommit psycopg connection; use `conn.transaction()` for batches."""

    def __init__(self, conn: psycopg.Connection[Any]) -> None:
        self.conn = conn

    @classmethod
    def connect(cls, conninfo: str) -> CaptureDB:
        return cls(psycopg.connect(conninfo, autocommit=True))

    def close(self) -> None:
        self.conn.close()

    def upsert_run(self, run: Run) -> None:
        self.conn.execute(
            """
            INSERT INTO runs (id, schema_version, job, started_at, finished_at, status,
                              code_commit, config, counts, prompt_versions, model_versions, error)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                finished_at = EXCLUDED.finished_at, status = EXCLUDED.status,
                counts = EXCLUDED.counts, error = EXCLUDED.error
            """,
            (
                run.id,
                run.schema_version,
                run.job,
                run.started_at,
                run.finished_at,
                run.status,
                run.code_commit,
                Jsonb(run.config),
                Jsonb(run.counts),
                Jsonb(run.prompt_versions) if run.prompt_versions is not None else None,
                Jsonb(run.model_versions) if run.model_versions is not None else None,
                run.error,
            ),
        )

    def upsert_evidence(self, ev: Evidence) -> None:
        self.conn.execute(
            """
            INSERT INTO evidence (id, schema_version, source, url, fetched_at, content_hash,
                snapshot_ref, content_type, http_status, reliability, terms_basis,
                retention_class, deletion_state, collector_version, case_id, repo_id, run_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO NOTHING
            """,
            (
                ev.id,
                ev.schema_version,
                ev.source,
                ev.url,
                ev.fetched_at,
                ev.content_hash,
                ev.snapshot_ref,
                ev.content_type,
                ev.http_status,
                ev.reliability,
                ev.terms_basis,
                ev.retention_class,
                ev.deletion_state,
                ev.collector_version,
                ev.case_id,
                ev.repo_id,
                ev.run_id,
            ),
        )

    def upsert_repo(self, repo: Repo) -> None:
        self.conn.execute(
            """
            INSERT INTO repos (id, schema_version, host, host_id, full_name, first_seen_at,
                               created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                full_name = EXCLUDED.full_name,
                first_seen_at = LEAST(repos.first_seen_at, EXCLUDED.first_seen_at),
                created_at = COALESCE(repos.created_at, EXCLUDED.created_at),
                updated_at = now()
            """,
            (
                repo.id,
                repo.schema_version,
                repo.host,
                repo.host_id,
                repo.full_name,
                repo.first_seen_at,
                repo.created_at,
            ),
        )

    def insert_case(self, case: Case) -> bool:
        """Insert a case; False if it already exists (same id or same repo/trigger/opened_at)."""
        cur = self.conn.execute(
            """
            INSERT INTO cases (id, schema_version, repo_id, opened_at, closed_at, trigger,
                               status, run_id, detection)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT DO NOTHING
            """,
            (
                case.id,
                case.schema_version,
                case.repo_id,
                case.opened_at,
                case.closed_at,
                case.trigger,
                case.status,
                case.run_id,
                Jsonb(case.detection.model_dump(mode="json")) if case.detection else None,
            ),
        )
        return cur.rowcount == 1

    def case_near(self, repo_id: str, trigger: str, at: datetime, cooldown_hours: int) -> bool:
        row = self.conn.execute(
            """
            SELECT 1 FROM cases
            WHERE repo_id = %s AND trigger = %s
              AND opened_at > %s - make_interval(hours => %s)
              AND opened_at < %s + make_interval(hours => %s)
            LIMIT 1
            """,
            (repo_id, trigger, at, cooldown_hours, at, cooldown_hours),
        ).fetchone()
        return row is not None

    def get_cases(self) -> list[Case]:
        rows = self.conn.execute(
            "SELECT id, repo_id, opened_at, closed_at, trigger, status, run_id, detection "
            "FROM cases ORDER BY opened_at, id"
        ).fetchall()
        return [
            Case(
                id=r[0],
                repo_id=r[1],
                opened_at=r[2],
                closed_at=r[3],
                trigger=r[4],
                status=r[5],
                run_id=r[6],
                detection=r[7],
            )
            for r in rows
        ]
