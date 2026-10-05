#!/usr/bin/env python3
"""Fail if staged/tracked files contain likely secrets or local credentials. Run before every commit."""

from __future__ import annotations

import re
import subprocess
import sys

PATTERNS = {
    "anthropic key": re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}"),
    "github token": re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})"),
    "openai-style key": re.compile(r"\bsk-[A-Za-z0-9]{32,}"),
    "aws key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "private key": re.compile(r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "bearer token": re.compile(r"Authorization:\s*Bearer\s+[A-Za-z0-9._-]{20,}"),
}
FORBIDDEN_FILES = re.compile(r"(^|/)\.env($|\.)(?!example)")


def files() -> list[str]:
    out = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"],
                         capture_output=True, text=True, check=True).stdout.split("\n")
    return [f for f in out if f]


def main() -> int:
    bad = []
    for f in files():
        if FORBIDDEN_FILES.search(f):
            bad.append(f"{f}: credentials file would be committed")
            continue
        try:
            text = open(f, encoding="utf-8", errors="ignore").read()
        except (IsADirectoryError, FileNotFoundError):
            continue
        for name, pat in PATTERNS.items():
            for m in pat.finditer(text):
                line = text[: m.start()].count("\n") + 1
                if "gitleaks:allow" in text.splitlines()[line - 1]:
                    continue
                bad.append(f"{f}:{line}: possible {name}")
    if bad:
        print("Secret scan FAILED:")
        print("\n".join(bad))
        return 1
    print(f"Secret scan passed ({len(files())} files).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
