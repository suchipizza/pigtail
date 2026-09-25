"""M1-T12 / R13.3 / CB-19: password hashing, client keys and UI settings (no database)."""

from __future__ import annotations

import io

import pytest

from pigtail.api.auth import client_key, hash_password, is_loopback_host, verify_password
from pigtail.api.settings import PASSWORD_HASH_ENV, UISettings
from pigtail.cli import main


def test_r13_3_argon2_hash_roundtrip_and_minimum_length():
    h = hash_password("a-long-enough-password")
    assert h.startswith("$argon2id$")
    assert verify_password(h, "a-long-enough-password")
    assert not verify_password(h, "a-long-enough-passwore")
    assert not verify_password("not-a-hash", "x")
    with pytest.raises(ValueError):
        hash_password("short")


def test_cb19_client_key_truncates_and_hides_the_address():
    secret = "$argon2id$v=19$m=65536,t=3,p=4$c2FsdA$aGFzaA"
    a = client_key("203.0.113.7", secret)
    assert a == client_key("203.0.113.200", secret)  # same /24
    assert a != client_key("203.0.114.7", secret)
    assert a != client_key("203.0.113.7", secret + "x")  # keyed per deployment
    assert len(a) == 16 and "203" not in a
    v6 = client_key("2001:db8:1:2::1", secret)
    assert v6 == client_key("2001:db8:1:ffff::9", secret)  # same /48
    assert client_key(None, secret) == client_key("testclient", secret)  # shared bucket


def test_r13_3_loopback_detection():
    for host in ("localhost", "localhost:8080", "127.0.0.1:8080", "[::1]:8080", "::1"):
        assert is_loopback_host(host), host
    for host in ("pigtail.example.org", "10.0.0.2:8080", "", None):
        assert not is_loopback_host(host), host


def test_r13_3_settings_refuse_to_start_without_a_password_hash():
    with pytest.raises(ValueError, match="private by default"):
        UISettings.from_env({})
    with pytest.raises(ValueError, match="argon2"):
        UISettings.from_env({PASSWORD_HASH_ENV: "plaintext"})
    s = UISettings.from_env({PASSWORD_HASH_ENV: "$argon2id$x", "PIGTAIL_UI_SECURE_COOKIE": "1"})
    assert s.secure_cookie is True and "argon2" not in repr(s)
    assert UISettings.from_env({PASSWORD_HASH_ENV: "$argon2id$x"}).secure_cookie is None


def test_r13_3_cli_hash_password_reads_stdin(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("a-long-enough-password\n"))
    assert main(["ui", "hash-password"]) == 0
    out = capsys.readouterr().out.strip()
    assert verify_password(out, "a-long-enough-password")
    monkeypatch.setattr("sys.stdin", io.StringIO("short\n"))
    assert main(["ui", "hash-password"]) == 2
