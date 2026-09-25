"""Content-addressed snapshot store (R1.4, PRD §5 "snapshot or drop").

Raw bytes may be dropped later (`delete()`: retention, deletion sync R1.5) while the sidecar and
the `evidence` record (url, hash, fetch time) are kept; replay then re-downloads and re-verifies.

Raw bytes are stored under `sha256/<h[0:2]>/<h[2:4]>/<h>` with a sidecar `<key>.meta.json`
holding the metadata of the **first** capture (source, url, fetched_at, collector_version,
terms_basis, content_type). Later captures of identical bytes are not rewritten; each capture
still gets its own `evidence` record with its own url and fetch time.

`get()` re-hashes the bytes and raises `SnapshotIntegrityError` on mismatch.

Backends: `LocalSnapshotStore` (filesystem under PIGTAIL_DATA_DIR/snapshots, dev and tests) and
`S3SnapshotStore` (any S3-compatible private bucket). Snapshots contain raw person-level data and
must never be written inside a git-tracked path.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

    from pigtail.config import Settings


class SnapshotError(RuntimeError):
    pass


class SnapshotNotFound(SnapshotError, KeyError):
    pass


class SnapshotIntegrityError(SnapshotError):
    pass


@dataclass(frozen=True)
class SnapshotMeta:
    source: str
    url: str
    fetched_at: datetime
    collector_version: str
    terms_basis: str
    content_type: str | None = None

    def to_json(self) -> dict[str, Any]:
        d = asdict(self)
        d["fetched_at"] = self.fetched_at.isoformat()
        return d

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> SnapshotMeta:
        return cls(**{**d, "fetched_at": datetime.fromisoformat(d["fetched_at"])})


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def key_for(content_hash: str) -> str:
    _check_hash(content_hash)
    return f"sha256/{content_hash[:2]}/{content_hash[2:4]}/{content_hash}"


def _check_hash(h: str) -> None:
    if len(h) != 64 or any(c not in "0123456789abcdef" for c in h):
        raise ValueError(f"not a lowercase sha256 hex digest: {h[:16]!r}")


class SnapshotStore(Protocol):
    def put(self, data: bytes, meta: SnapshotMeta) -> str:
        """Store bytes (idempotent); return the SHA-256 hex digest."""
        ...

    def get(self, content_hash: str) -> bytes:
        """Return the bytes, verified against the hash."""
        ...

    def exists(self, content_hash: str) -> bool: ...

    def delete(self, content_hash: str) -> bool:
        """Drop the raw bytes (retention / deletion sync); keep the metadata sidecar.

        Returns True if bytes were removed. Idempotent.
        """
        ...

    def meta(self, content_hash: str) -> SnapshotMeta: ...

    def ref(self, content_hash: str) -> str:
        """Location string for `evidence.snapshot_ref`."""
        ...


def _verified(data: bytes, content_hash: str) -> bytes:
    if sha256_hex(data) != content_hash:
        raise SnapshotIntegrityError(f"snapshot {content_hash[:12]}… failed hash verification")
    return data


class LocalSnapshotStore:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    def _path(self, content_hash: str) -> Path:
        return self.root / key_for(content_hash)

    @staticmethod
    def _atomic_write(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def put(self, data: bytes, meta: SnapshotMeta) -> str:
        h = sha256_hex(data)
        path = self._path(h)
        if not path.exists():
            self._atomic_write(path, data)
        side = path.with_name(path.name + ".meta.json")
        if not side.exists():
            self._atomic_write(side, json.dumps(meta.to_json(), sort_keys=True).encode())
        return h

    def get(self, content_hash: str) -> bytes:
        path = self._path(content_hash)
        try:
            data = path.read_bytes()
        except FileNotFoundError as e:
            raise SnapshotNotFound(content_hash) from e
        return _verified(data, content_hash)

    def exists(self, content_hash: str) -> bool:
        return self._path(content_hash).exists()

    def delete(self, content_hash: str) -> bool:
        path = self._path(content_hash)
        existed = path.exists()
        path.unlink(missing_ok=True)
        return existed

    def meta(self, content_hash: str) -> SnapshotMeta:
        side = self._path(content_hash).with_name(content_hash + ".meta.json")
        try:
            return SnapshotMeta.from_json(json.loads(side.read_text()))
        except FileNotFoundError as e:
            raise SnapshotNotFound(content_hash) from e

    def ref(self, content_hash: str) -> str:
        return "local:" + key_for(content_hash)


class S3SnapshotStore:
    """S3-compatible backend (boto3). Credentials come only from env/settings."""

    def __init__(self, client: S3Client, bucket: str) -> None:
        self.client = client
        self.bucket = bucket

    @classmethod
    def from_settings(cls, s: Settings) -> S3SnapshotStore:
        import boto3
        from botocore.config import Config

        client = boto3.client(
            "s3",
            endpoint_url=s.s3_endpoint,
            aws_access_key_id=s.s3_access_key,
            aws_secret_access_key=s.s3_secret_key,
            region_name=s.s3_region,
            config=Config(
                s3={"addressing_style": "path"},
                retries={"max_attempts": 5, "mode": "standard"},
            ),
        )
        return cls(client, s.s3_bucket)

    def _head(self, key: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return False
            raise

    def put(self, data: bytes, meta: SnapshotMeta) -> str:
        h = sha256_hex(data)
        key = key_for(h)
        if not self._head(key):
            self.client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=data,
                ContentType=meta.content_type or "application/octet-stream",
                Metadata={"sha256": h, "source": meta.source},
            )
        if not self._head(key + ".meta.json"):
            self.client.put_object(
                Bucket=self.bucket,
                Key=key + ".meta.json",
                Body=json.dumps(meta.to_json(), sort_keys=True).encode(),
                ContentType="application/json",
            )
        return h

    def _read(self, key: str, content_hash: str) -> bytes:
        from botocore.exceptions import ClientError

        try:
            obj = self.client.get_object(Bucket=self.bucket, Key=key)
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                raise SnapshotNotFound(content_hash) from e
            raise
        body: bytes = obj["Body"].read()
        return body

    def get(self, content_hash: str) -> bytes:
        return _verified(self._read(key_for(content_hash), content_hash), content_hash)

    def exists(self, content_hash: str) -> bool:
        return self._head(key_for(content_hash))

    def delete(self, content_hash: str) -> bool:
        key = key_for(content_hash)
        existed = self._head(key)
        if existed:
            self.client.delete_object(Bucket=self.bucket, Key=key)
        return existed

    def meta(self, content_hash: str) -> SnapshotMeta:
        raw = self._read(key_for(content_hash) + ".meta.json", content_hash)
        return SnapshotMeta.from_json(json.loads(raw))

    def ref(self, content_hash: str) -> str:
        return f"s3://{self.bucket}/{key_for(content_hash)}"


def build_store(s: Settings) -> SnapshotStore:
    """`SNAPSHOT_BACKEND=local` (default; PIGTAIL_DATA_DIR/snapshots) or `s3`."""
    if s.snapshot_backend == "s3":
        return S3SnapshotStore.from_settings(s)
    return LocalSnapshotStore(s.data_dir / "snapshots")
