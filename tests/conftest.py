from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel

from pigtail.llm import LLMClient, PromptSpec
from pigtail.llm.redact import alias_redact
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
        self,
        *,
        system: str,
        prompt: str,
        json_schema: dict[str, Any],
        model: str,
        context: str = "",
        thinking: str = "disabled",
    ) -> BackendResponse:
        self.calls.append(
            {
                "system": system,
                "prompt": prompt,
                "schema": json_schema,
                "model": model,
                "context": context,
                "thinking": thinking,
            }
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
        redactor=alias_redact,
        **kw,
    )


# --- hermetic environment (verifier M24 round 2): the instance's .env never reaches a test ------
# An operator who runs the suite in a shell with the instance's `.env` loaded would otherwise leak
# its backend, models, keys, tokens, data directories, caps and connector flags into tests that
# assume the code defaults (20 failures seen). Cleared once, at collection, before any test
# module reads them. Kept: the test controls (the database and object-store endpoints the
# fixtures use, PIGTAIL_REQUIRE_*, PIGTAIL_RUN_SMOKE) and, only for an explicit smoke run, the API
# key the live smoke tests read.
_ENV_PREFIXES = (
    "PIGTAIL_",
    "LLM_",
    "ANTHROPIC_",
    "CLAUDE_CODE_",
    "GITHUB_",
    "GCP_",
    "GOOGLE_",
    "REDDIT_",
    "PH_",
    "BACKUP_",
    "ALERT_",
    "SMTP_",
    "COMPOSE_",
    "OPTOUT_",
    "PSEUDONYM_",
)
_ENV_NAMES = (
    "AGENT_BACKEND",
    "SNAPSHOT_BACKEND",
    "BUDGET_USD_MONTH",
    "SNAPSHOT_AFTER_REPORT_DAYS",
    "PERSON_LEVEL_RETENTION_DAYS",
    "LLM_CACHE_RETENTION_DAYS",
    "LOG_RETENTION_DAYS",
    "GHARCHIVE_RAW_RETENTION_DAYS",
    "S3_SSE_KEK",
)
_ENV_KEEP = ("PIGTAIL_REQUIRE_DB", "PIGTAIL_REQUIRE_S3", "PIGTAIL_RUN_SMOKE")


def _hermetic_env() -> list[str]:
    import os as _os

    smoke = _os.environ.get("PIGTAIL_RUN_SMOKE") == "1"
    dropped = []
    for k in list(_os.environ):
        if k in _ENV_KEEP or k.startswith("PIGTAIL_REQUIRE_"):
            continue
        if smoke and k == "ANTHROPIC_API_KEY":
            continue
        if k in _ENV_NAMES or k.startswith(_ENV_PREFIXES):
            del _os.environ[k]
            dropped.append(k)
    return dropped


HERMETIC_DROPPED = _hermetic_env()


# --- M21b (ADR-071.3): tests never touch the real briefs directory -----------------------------
@pytest.fixture(autouse=True)
def _isolated_briefs_dir(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch):
    """The default briefs dir is ~/.pigtail/briefs; every test gets a private one instead
    (env and code default), so no test can write into the operator's real briefs."""
    import pigtail.config as config

    d = tmp_path_factory.mktemp("briefs-home") / ".pigtail" / "briefs"
    monkeypatch.setattr(config, "DEFAULT_BRIEFS_DIR", str(d))
    monkeypatch.setenv("PIGTAIL_BRIEFS_DIR", str(d))
    monkeypatch.delenv("PIGTAIL_BRIEFS_IN_DATA_DIR", raising=False)
    return d


# --- ADR-085: view B's Product Hunt and Bluesky launch sources ---------------------------------
@pytest.fixture(autouse=True)
def _launch_sources_off() -> Iterator[None]:
    """The instance flags turn both launch sources on by default (fail closed without their
    connectors). Tests written before ADR-085 run the selection without them, so the default
    for the suite is off; the ADR-085 tests turn them back on (`launch_sources_on`). A real
    token or base URL from the developer's environment never reaches a test. Its own
    MonkeyPatch, so a test's `monkeypatch.undo()` keeps it."""
    mp = pytest.MonkeyPatch()
    mp.setenv("PIGTAIL_SELECTION_PRODUCT_HUNT", "false")
    mp.setenv("PIGTAIL_SELECTION_BLUESKY", "false")
    for k in ("PH_API_TOKEN", "PIGTAIL_BLUESKY_API_BASE", "PIGTAIL_PH_TOPICS"):
        mp.delenv(k, raising=False)
    yield
    mp.undo()


@pytest.fixture
def launch_sources_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both launch sources as the defaults have them: on (ADR-085)."""
    monkeypatch.delenv("PIGTAIL_SELECTION_PRODUCT_HUNT", raising=False)
    monkeypatch.delenv("PIGTAIL_SELECTION_BLUESKY", raising=False)


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
