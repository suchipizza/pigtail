from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel

from pigtail.llm import LLMClient, PromptSpec
from pigtail.llm.store import LLMStore
from pigtail.llm.types import BackendResponse
from pigtail.pseudonymize import Pseudonymizer

TEST_KEY = "test-key-not-secret-0123456789"


class Echo(BaseModel):
    value: int
    label: str


class FakeBackend:
    """Scripted backend: each call pops the next item (a dict to return or an exception)."""

    def __init__(self, name: str, script: list[Any]) -> None:
        self.name = name
        self.script = list(script)
        self.calls: list[dict[str, Any]] = []

    def complete(
        self, *, system: str, prompt: str, json_schema: dict[str, Any], model: str
    ) -> BackendResponse:
        self.calls.append(
            {"system": system, "prompt": prompt, "schema": json_schema, "model": model}
        )
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return BackendResponse(
            data=item, model=model, input_tokens=10, output_tokens=5, cost_usd=0.001
        )


@pytest.fixture
def prompt() -> PromptSpec:
    return PromptSpec(id="echo", version="1", system="sys", template="Input: {input}")


@pytest.fixture
def pz() -> Pseudonymizer:
    return Pseudonymizer(TEST_KEY)


def make_client(sub: list[Any], api: list[Any] | None = None, **kw: Any) -> LLMClient:
    backends = {"subscription": FakeBackend("subscription", sub)}
    if api is not None:
        backends["api"] = FakeBackend("api", api)
    return LLMClient(
        backends=backends,
        default_backend=kw.pop("default_backend", "subscription"),
        store=LLMStore(":memory:"),
        model="test-model",
        redactor=Pseudonymizer(TEST_KEY).strip_identifiers,
        **kw,
    )


# --- Postgres / S3 fixtures (M1-T1) ------------------------------------------------------------
import os  # noqa: E402
import uuid  # noqa: E402
from collections.abc import Iterator  # noqa: E402
from pathlib import Path  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"
DEFAULT_DB = "postgresql://pigtail:pigtail@localhost:5432/pigtail"


def _unavailable(what: str, env_flag: str, err: Exception) -> None:
    if os.environ.get(env_flag) == "1":
        pytest.fail(f"{what} required ({env_flag}=1) but unreachable: {err}")
    pytest.skip(f"{what} unreachable: {err}")


@pytest.fixture(scope="session")
def pg_admin_url() -> str:
    import psycopg

    url = os.environ.get("DATABASE_URL", DEFAULT_DB)
    try:
        psycopg.connect(url, connect_timeout=3).close()
    except Exception as e:  # pragma: no cover - depends on environment
        _unavailable("Postgres", "PIGTAIL_REQUIRE_DB", e)
    return url


@pytest.fixture
def pg_url(pg_admin_url: str) -> Iterator[str]:
    """A throwaway database `pigtail_test_<random>`, dropped afterwards."""
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo

    name = f"pigtail_test_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(pg_admin_url, autocommit=True) as c:
        c.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        yield make_conninfo(pg_admin_url, dbname=name)
    finally:
        with psycopg.connect(pg_admin_url, autocommit=True) as c:
            c.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
            )


@pytest.fixture
def capture_db(pg_url: str) -> Iterator[Any]:
    from pigtail.capture.db import CaptureDB
    from pigtail.db.migrate import migrate

    migrate(pg_url)
    db = CaptureDB.connect(pg_url)
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(scope="session")
def s3_store() -> Any:
    from pigtail.capture.snapshots import S3SnapshotStore
    from pigtail.config import Settings

    env = {
        "S3_ENDPOINT": "http://localhost:9000",
        "S3_ACCESS_KEY": "pigtail-local",
        "S3_SECRET_KEY": "pigtail-local-secret",
        "S3_BUCKET": "pigtail-snapshots",
        **{k: v for k, v in os.environ.items() if k.startswith("S3_")},
    }
    store = S3SnapshotStore.from_settings(Settings.from_env(env))
    try:
        import socket
        from urllib.parse import urlparse

        u = urlparse(env["S3_ENDPOINT"])
        socket.create_connection((u.hostname or "localhost", u.port or 80), timeout=2).close()
        store.client.head_bucket(Bucket=store.bucket)
    except Exception as e:  # pragma: no cover - depends on environment
        _unavailable("S3 store", "PIGTAIL_REQUIRE_S3", e)
    return store
