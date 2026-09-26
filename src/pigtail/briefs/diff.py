"""Diff between two brief versions (R18.4, D7 "a diff view compares any two versions").

`diff_briefs` returns field-level changes (dotted paths; lists compared as a whole, with the
items added and removed) and a unified text diff of the canonical YAML. Store-managed metadata
(version, times) is left out of the field changes.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from typing import Any, Literal

from pigtail.briefs.model import Brief, dump_yaml

ChangeKind = Literal["added", "removed", "changed"]


@dataclass(frozen=True)
class Change:
    path: str
    kind: ChangeKind
    old: Any = None
    new: Any = None
    items_added: list[Any] = field(default_factory=list)
    items_removed: list[Any] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"path": self.path, "kind": self.kind, "old": self.old, "new": self.new}
        if self.items_added or self.items_removed:
            d["items_added"] = self.items_added
            d["items_removed"] = self.items_removed
        return d


def flatten(d: Any, prefix: str = "") -> dict[str, Any]:
    """Nested mappings → {dotted.path: leaf}; lists are leaves."""
    if isinstance(d, dict):
        out: dict[str, Any] = {}
        if not d and prefix:
            out[prefix] = {}
        for k, v in d.items():
            out.update(flatten(v, f"{prefix}.{k}" if prefix else str(k)))
        return out
    return {prefix: d}


def field_changes(a: dict[str, Any], b: dict[str, Any]) -> list[Change]:
    fa, fb = flatten(a), flatten(b)
    out: list[Change] = []
    for path in sorted(set(fa) | set(fb)):
        if path not in fb:
            out.append(Change(path, "removed", old=fa[path]))
        elif path not in fa:
            out.append(Change(path, "added", new=fb[path]))
        elif fa[path] != fb[path]:
            old, new = fa[path], fb[path]
            if isinstance(old, list) and isinstance(new, list):
                out.append(
                    Change(
                        path,
                        "changed",
                        old,
                        new,
                        items_added=[x for x in new if x not in old],
                        items_removed=[x for x in old if x not in new],
                    )
                )
            else:
                out.append(Change(path, "changed", old, new))
    return out


@dataclass(frozen=True)
class BriefDiff:
    brief_id: str
    from_version: int | None
    to_version: int | None
    changes: list[Change]
    unified: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "brief_id": self.brief_id,
            "from_version": self.from_version,
            "to_version": self.to_version,
            "changes": [c.to_dict() for c in self.changes],
            "unified": self.unified,
        }


def diff_briefs(a: Brief, b: Brief) -> BriefDiff:
    ya, yb = dump_yaml(a), dump_yaml(b)
    unified = "".join(
        difflib.unified_diff(
            ya.splitlines(keepends=True),
            yb.splitlines(keepends=True),
            fromfile=f"{a.brief_id} v{a.version}",
            tofile=f"{b.brief_id} v{b.version}",
        )
    )
    return BriefDiff(
        b.brief_id, a.version, b.version, field_changes(a.content(), b.content()), unified
    )
