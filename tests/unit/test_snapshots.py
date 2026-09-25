"""M1-T1 / R1.4: content-addressed snapshot store (local + S3 backends)."""

from __future__ import annotations

import hashlib
import os
from datetime import UTC, datetime

import pytest

from pigtail.capture.snapshots import (
    LocalSnapshotStore,
    SnapshotIntegrityError,
    SnapshotMeta,
    SnapshotNotFound,
    build_store,
    key_for,
)
from pigtail.config import Settings

META = SnapshotMeta(
    source="test",
    url="https://example.org/a",
    fetched_at=datetime(2026, 9, 20, 12, tzinfo=UTC),
    collector_version="test/0.0.1",
    terms_basis="synthetic",
    content_type="application/json",
)


def test_r1_4_key_layout():
    h = hashlib.sha256(b"x").hexdigest()
    assert key_for(h) == f"sha256/{h[:2]}/{h[2:4]}/{h}"
    with pytest.raises(ValueError):
        key_for("NOTAHASH")


def test_r1_4_local_put_get_meta_and_ref(tmp_path):
    store = LocalSnapshotStore(tmp_path)
    data = b'{"hello": "world"}'
    h = store.put(data, META)
    assert h == hashlib.sha256(data).hexdigest()
    assert (tmp_path / key_for(h)).read_bytes() == data
    assert store.get(h) == data
    assert store.exists(h)
    assert store.meta(h) == META
    assert store.ref(h) == f"local:{key_for(h)}"


def test_r1_4_local_put_is_idempotent_and_keeps_first_meta(tmp_path):
    store = LocalSnapshotStore(tmp_path)
    h = store.put(b"same", META)
    path = tmp_path / key_for(h)
    os.utime(path, (1, 1))
    other = SnapshotMeta(**{**META.__dict__, "url": "https://example.org/b"})
    assert store.put(b"same", other) == h
    assert path.stat().st_mtime == 1  # not rewritten
    assert store.meta(h).url == META.url
    files = [p for p in tmp_path.rglob("*") if p.is_file()]
    assert len(files) == 2  # object + sidecar


def test_r1_4_local_get_verifies_hash(tmp_path):
    store = LocalSnapshotStore(tmp_path)
    h = store.put(b"original", META)
    (tmp_path / key_for(h)).write_bytes(b"tampered")
    with pytest.raises(SnapshotIntegrityError):
        store.get(h)


def test_local_missing(tmp_path):
    store = LocalSnapshotStore(tmp_path)
    h = hashlib.sha256(b"nope").hexdigest()
    assert not store.exists(h)
    with pytest.raises(SnapshotNotFound):
        store.get(h)
    with pytest.raises(SnapshotNotFound):
        store.meta(h)


def test_build_store_defaults_to_local_under_data_dir(tmp_path):
    s = Settings.from_env({"PIGTAIL_DATA_DIR": str(tmp_path)})
    store = build_store(s)
    assert isinstance(store, LocalSnapshotStore)
    assert store.root == tmp_path / "snapshots"
    with pytest.raises(ValueError):
        Settings.from_env({"SNAPSHOT_BACKEND": "ftp"})


@pytest.mark.s3
def test_r1_4_s3_put_get_idempotent_and_verified(s3_store):
    data = os.urandom(64)
    h = s3_store.put(data, META)
    assert h == hashlib.sha256(data).hexdigest()
    assert s3_store.exists(h)
    assert s3_store.get(h) == data
    assert s3_store.meta(h) == META
    assert s3_store.ref(h) == f"s3://{s3_store.bucket}/{key_for(h)}"
    head = s3_store.client.head_object(Bucket=s3_store.bucket, Key=key_for(h))
    assert head["Metadata"]["sha256"] == h
    etag = head["ETag"]
    assert s3_store.put(data, META) == h
    assert s3_store.client.head_object(Bucket=s3_store.bucket, Key=key_for(h))["ETag"] == etag
    # tamper -> integrity error
    s3_store.client.put_object(Bucket=s3_store.bucket, Key=key_for(h), Body=b"tampered")
    with pytest.raises(SnapshotIntegrityError):
        s3_store.get(h)
    assert s3_store.delete(h) is True and not s3_store.exists(h)
    assert s3_store.meta(h) == META and s3_store.delete(h) is False
    missing = hashlib.sha256(os.urandom(16)).hexdigest()
    assert not s3_store.exists(missing)
    with pytest.raises(SnapshotNotFound):
        s3_store.get(missing)


def test_cb04_local_delete_keeps_metadata(tmp_path):
    store = LocalSnapshotStore(tmp_path)
    h = store.put(b"raw", META)
    assert store.delete(h) is True
    assert not store.exists(h)
    assert store.meta(h) == META
    assert store.delete(h) is False
    with pytest.raises(SnapshotNotFound):
        store.get(h)
