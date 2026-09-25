"""DPIA CB-03: `pigtail doctor` reports encryption at rest for the snapshot bucket."""

from __future__ import annotations

import json
import os

import boto3
import pytest
from botocore.stub import Stubber

from pigtail.cli import main
from pigtail.config import Settings
from pigtail.privacy.doctor import check_bucket_encryption, exit_code, run_checks

KEY = "k" * 40


def stubbed():
    client = boto3.client(
        "s3",
        region_name="us-east-1",
        aws_access_key_id="test",
        aws_secret_access_key="test-secret",
    )
    return client, Stubber(client)


def test_cb03_bucket_with_default_encryption_is_ok():
    client, stub = stubbed()
    stub.add_response(
        "get_bucket_encryption",
        {
            "ServerSideEncryptionConfiguration": {
                "Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]
            }
        },
        {"Bucket": "b"},
    )
    with stub:
        c = check_bucket_encryption(client, "b")
    assert c.status == "ok" and "AES256" in c.detail


def test_cb03_bucket_without_encryption_warns():
    client, stub = stubbed()
    stub.add_client_error(
        "get_bucket_encryption",
        service_error_code="ServerSideEncryptionConfigurationNotFoundError",
        http_status_code=404,
    )
    with stub:
        c = check_bucket_encryption(client, "b")
    assert c.status == "warn" and "no default encryption" in c.detail


def test_cb03_store_without_the_api_needs_manual_check():
    client, stub = stubbed()
    stub.add_client_error("get_bucket_encryption", service_error_code="NotImplemented")
    with stub:
        assert check_bucket_encryption(client, "b").status == "manual"


def test_cb03_local_backend_and_postgres_are_manual_checks():
    checks = {
        c.name: c for c in run_checks(Settings.from_env({"PSEUDONYM_KEY": KEY}), db_check=False)
    }
    assert checks["snapshot_bucket_encryption"].status == "manual"
    assert checks["postgres_volume_encryption"].status == "manual"
    assert checks["pseudonym_key"].status == "ok"
    assert checks["database"].status == "fail"  # DATABASE_URL not set


def test_cb03_key_checks_and_exit_codes():
    short = {c.name: c for c in run_checks(Settings.from_env({"PSEUDONYM_KEY": "k" * 20}))}
    assert short["pseudonym_key"].status == "warn"
    none = run_checks(Settings.from_env({}), db_check=False)
    assert exit_code(none) == 1
    s = Settings.from_env({"PSEUDONYM_KEY": "k" * 20})
    warn_only = [c for c in run_checks(s, db_check=False) if c.name == "pseudonym_key"]
    assert exit_code(warn_only) == 0 and exit_code(warn_only, strict=True) == 1


def test_cb03_plain_http_to_remote_store_warns():
    client, stub = stubbed()
    stub.add_client_error(
        "get_bucket_encryption",
        service_error_code="ServerSideEncryptionConfigurationNotFoundError",
    )
    env = {
        "PSEUDONYM_KEY": KEY,
        "SNAPSHOT_BACKEND": "s3",
        "S3_ENDPOINT": "http://storage.internal.example:9000",
    }
    with stub:
        checks = {c.name: c for c in run_checks(Settings.from_env(env), s3_client=client)}
    assert checks["snapshot_transport"].status == "warn"


@pytest.mark.s3
def test_cb03_doctor_against_local_seaweedfs(s3_store, monkeypatch, capsys):
    """Real GetBucketEncryption call on the compose object store (SSE is optional locally)."""
    c = check_bucket_encryption(s3_store.client, s3_store.bucket)
    assert c.status in ("ok", "warn")
    monkeypatch.setenv("PSEUDONYM_KEY", KEY)
    monkeypatch.setenv("SNAPSHOT_BACKEND", "s3")
    monkeypatch.setenv("S3_ENDPOINT", s3_store.client.meta.endpoint_url)
    monkeypatch.setenv("S3_BUCKET", s3_store.bucket)
    for var, default in (
        ("S3_ACCESS_KEY", "pigtail-local"),
        ("S3_SECRET_KEY", "pigtail-local-secret"),
    ):
        monkeypatch.setenv(var, os.environ.get(var, default))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    rc = main(["doctor", "--json"])
    out = {c["name"]: c for c in json.loads(capsys.readouterr().out)}
    assert out["snapshot_bucket_encryption"]["status"] == c.status
    assert rc == 1  # DATABASE_URL missing is a failure
