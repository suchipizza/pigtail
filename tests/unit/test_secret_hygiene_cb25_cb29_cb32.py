"""CB-25 fingerprint properties, CB-29 secret hygiene (Settings repr, compose `ui` env) and the
CB-32 GH Archive raw-retention ceiling. Synthetic values only."""

from __future__ import annotations

from pathlib import Path

import pytest

from pigtail.cli import main
from pigtail.config import GHARCHIVE_RAW_MAX_DAYS, Settings
from pigtail.pseudonymize import KEY_FINGERPRINT_RE, Pseudonymizer
from pigtail.scheduler.alerts import parse_smtp_url
from tests.conftest import TEST_KEY

REPO = Path(__file__).resolve().parents[2]
SECRETS = {
    "PSEUDONYM_KEY": "pk-synthetic-secret-value-0001",
    "DATABASE_URL": "postgresql://pigtail:dbpw-synthetic-0002@localhost:5432/pigtail",
    "S3_ACCESS_KEY": "s3-access-synthetic-0003",
    "S3_SECRET_KEY": "s3-secret-synthetic-0004",
}


# --- CB-25 --------------------------------------------------------------------------------------
def test_cb25_fingerprint_is_stable_keyed_and_reveals_no_key():
    a, b = Pseudonymizer(TEST_KEY), Pseudonymizer(TEST_KEY + "x")
    assert a.fingerprint() == Pseudonymizer(TEST_KEY).fingerprint()
    assert a.fingerprint() != b.fingerprint()
    assert KEY_FINGERPRINT_RE.match(a.fingerprint())
    assert TEST_KEY not in a.fingerprint()
    # a fixed label without ':' never collides with a pseudonym or keyed_hex input
    assert a.fingerprint()[5:] != a.keyed_hex("pigtail-key-fingerprint-v1", "generic")[:32]


# --- CB-29 --------------------------------------------------------------------------------------
def test_cb29_settings_repr_contains_no_secret_values():
    s = Settings.from_env(dict(SECRETS))
    for text in (repr(s), str(s), f"{s!r}"):
        for value in SECRETS.values():
            assert value not in text
        assert "dbpw-synthetic" not in text
        assert "pseudonym_key='***'" in text and "s3_secret_key='***'" in text
    assert s.pseudonym_key == SECRETS["PSEUDONYM_KEY"]  # masked in repr only
    assert "pseudonym_key=None" in repr(Settings.from_env({}))


def test_cb29_smtp_password_not_in_repr():
    t = parse_smtp_url("smtps://ops:smtp-pw-synthetic-0005@localhost:465")
    assert t.password == "smtp-pw-synthetic-0005"
    assert "smtp-pw-synthetic-0005" not in repr(t)


def test_cb29_compose_ui_service_gets_no_pseudonym_key():
    yaml = pytest.importorskip("yaml")
    compose = yaml.safe_load((REPO / "docker-compose.yml").read_text())
    ui = compose["services"]["ui"]
    assert "env_file" not in ui  # the whole .env (with PSEUDONYM_KEY) is never loaded
    env = ui["environment"]
    names = set(env) if isinstance(env, dict) else {e.split("=", 1)[0] for e in env}
    for secret in ("PSEUDONYM_KEY", "GITHUB_TOKEN", "SMTP_URL", "ANTHROPIC_API_KEY", "S3_SSE_KEK"):
        assert secret not in names
    assert {"DATABASE_URL", "PIGTAIL_OPERATOR_PASSWORD_HASH", "LOG_RETENTION_DAYS"} <= names
    # the scheduler still pseudonymizes and keeps its env file
    assert "env_file" in compose["services"]["scheduler"]


def test_cb29_ui_code_never_reads_the_pseudonym_key():
    api = REPO / "src" / "pigtail" / "api"
    for f in api.glob("*.py"):
        text = f.read_text()
        assert "PSEUDONYM_KEY" not in text and "pseudonym_key" not in text, f.name
        assert "Pseudonymizer" not in text, f.name


# --- CB-32 --------------------------------------------------------------------------------------
def test_cb32_gharchive_raw_retention_ceiling():
    assert GHARCHIVE_RAW_MAX_DAYS == 30
    assert Settings.from_env({}).gharchive_raw_retention_days == 30
    assert (
        Settings.from_env({"GHARCHIVE_RAW_RETENTION_DAYS": "7"}).gharchive_raw_retention_days == 7
    )
    assert (
        Settings.from_env({"GHARCHIVE_RAW_RETENTION_DAYS": "0"}).gharchive_raw_retention_days == 0
    )
    for bad in ("31", "365", "-1"):
        with pytest.raises(ValueError, match="GHARCHIVE_RAW_RETENTION_DAYS"):
            Settings.from_env({"GHARCHIVE_RAW_RETENTION_DAYS": bad})


def test_cb32_purge_raw_cli_refuses_longer_retention(monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", "postgresql://nobody@127.0.0.1:1/none")
    monkeypatch.delenv("GHARCHIVE_RAW_RETENTION_DAYS", raising=False)
    assert main(["capture", "purge-raw", "--retention-days", "31"]) == 2
    assert "between 0 and 30" in capsys.readouterr().err
