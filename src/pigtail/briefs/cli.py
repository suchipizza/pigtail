"""`pigtail brief ...` (PRD F18, M12; D7).

    pigtail brief new --from FILE [--id ID] [--upgrade-v0]   create a brief (version 1)
    pigtail brief new --example --id ID                      start from the synthetic example
    pigtail brief edit ID [--from FILE] [--base-version N]   new version ($EDITOR without --from)
    pigtail brief show ID [--version N] [--json]
    pigtail brief list [--json]
    pigtail brief versions ID [--json]
    pigtail brief diff ID [A [B]] [--json]                   default: previous vs latest
    pigtail brief validate FILE|ID [--upgrade-v0] [--json]
    pigtail brief estimate ID [--version N] [--approve-paid] [--json]
    pigtail brief schema                                     print schemas/brief/v1.json

Exit codes: 0 ok; 1 invalid brief or not found; 2 usage/config error; 3 the estimate has paid
steps that were not approved (`--approve-paid`).
Briefs are private: they live in PIGTAIL_DATA_DIR/briefs and never in git (R18.9).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from pigtail.briefs.model import (
    Brief,
    BriefInvalid,
    json_schema,
    parse_yaml,
    upgrade_v0,
    validate_brief,
)
from pigtail.briefs.store import BriefExists, BriefNotFound, BriefStore, VersionConflict

EXIT_INVALID = 1
EXIT_USAGE = 2
EXIT_NEEDS_APPROVAL = 3


def example_path() -> Path:
    """The synthetic example brief (`docs/examples/brief-example.yaml`; env override for the
    container image, where the package is installed outside the repo)."""
    env = os.environ.get("PIGTAIL_EXAMPLE_BRIEF")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[3] / "docs" / "examples" / "brief-example.yaml"


def _settings() -> Any:
    from pigtail.config import Settings

    return Settings.from_env()


def _store() -> BriefStore:
    return BriefStore.from_data_dir(_settings().data_dir)


def _read_source(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    return Path(path).read_text(encoding="utf-8")


def _load(text: str, *, upgrade: bool, brief_id: str | None = None) -> Brief:
    data = parse_yaml(text)
    if upgrade and isinstance(data, dict):
        data = upgrade_v0(data)
    if brief_id is not None and isinstance(data, dict):
        data = {**data, "brief_id": brief_id}
    return validate_brief(data)


def _print_invalid(e: BriefInvalid) -> int:
    print(str(e), file=sys.stderr)
    return EXIT_INVALID


def _warn(brief: Brief, store: BriefStore | None = None) -> None:
    for w in brief.warnings():
        print(f"warning: {w}", file=sys.stderr)
    if store is not None and (exposed := store.exposure_warning()):
        print(f"warning: {exposed}", file=sys.stderr)


def _json(obj: Any) -> None:
    print(json.dumps(obj, indent=2, default=str))


# --- commands -----------------------------------------------------------------------------------


def cmd_new(args: argparse.Namespace) -> int:
    """R18.1/R18.3: create version 1 of a brief from YAML (or from the synthetic example)."""
    if bool(args.source) == bool(args.example):
        print("give exactly one of --from FILE or --example", file=sys.stderr)
        return EXIT_USAGE
    if args.example and not args.id:
        print("--example needs --id (your brief's id, e.g. my-project)", file=sys.stderr)
        return EXIT_USAGE
    text = _read_source(args.source) if args.source else example_path().read_text("utf-8")
    store = _store()
    try:
        brief = _load(text, upgrade=args.upgrade_v0, brief_id=args.id)
        stored = store.create(brief)
    except BriefInvalid as e:
        return _print_invalid(e)
    except BriefExists as e:
        print(str(e), file=sys.stderr)
        return EXIT_INVALID
    _warn(stored.brief, store)
    print(f"created {stored.brief.brief_id} v{stored.version} ({stored.path})")
    return 0


def _edit_in_editor(current: str, store: BriefStore) -> str | None:
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR")
    if not editor or not sys.stdin.isatty():
        print("no --from FILE given and no interactive $EDITOR", file=sys.stderr)
        return None
    store.root.mkdir(mode=0o700, parents=True, exist_ok=True)
    # The draft stays inside the private store (mode 0600) and is removed afterwards.
    fd, tmp = tempfile.mkstemp(prefix=".edit-", suffix=".yaml", dir=store.root)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(current)
        subprocess.run([*editor.split(), tmp], check=False)
        return Path(tmp).read_text(encoding="utf-8")
    finally:
        Path(tmp).unlink(missing_ok=True)


def cmd_edit(args: argparse.Namespace) -> int:
    """R18.4: every edit creates a new, immutable version; unchanged content is a no-op."""
    store = _store()
    try:
        latest = store.get(args.brief_id)
    except BriefNotFound as e:
        print(str(e), file=sys.stderr)
        return EXIT_INVALID
    if args.source:
        text = _read_source(args.source)
    else:
        edited = _edit_in_editor(latest.yaml_text, store)
        if edited is None:
            return EXIT_USAGE
        text = edited
    try:
        brief = _load(text, upgrade=False)
        if brief.brief_id != args.brief_id:
            print(
                f"brief_id in the file is {brief.brief_id!r}, not {args.brief_id!r}; "
                "to copy a brief under a new id use `pigtail brief new`",
                file=sys.stderr,
            )
            return EXIT_INVALID
        base = args.base_version if args.base_version is not None else latest.version
        stored, created = store.save_version(brief, base_version=base)
    except BriefInvalid as e:
        return _print_invalid(e)
    except VersionConflict as e:
        print(str(e), file=sys.stderr)
        return EXIT_INVALID
    _warn(stored.brief, store)
    if created:
        print(f"saved {stored.brief.brief_id} v{stored.version} (supersedes v{stored.version - 1})")
    else:
        print(f"no changes; {stored.brief.brief_id} stays at v{stored.version}")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    try:
        stored = _store().get(args.brief_id, args.version)
    except (BriefNotFound, BriefInvalid) as e:
        print(str(e), file=sys.stderr)
        return EXIT_INVALID
    if args.json:
        _json(stored.brief.model_dump(mode="json"))
    else:
        print(stored.yaml_text, end="")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    rows = _store().list()
    if args.json:
        _json([r.__dict__ for r in rows])
        return 0
    if not rows:
        print("no briefs yet: `pigtail brief new --example --id my-project` starts one")
        return 0
    for r in rows:
        edited = r.edited_at.isoformat() if r.edited_at else "-"
        print(f"{r.brief_id:<30} v{r.latest_version:<3} {r.status:<20} {edited}  {r.name}")
    return 0


def cmd_versions(args: argparse.Namespace) -> int:
    try:
        vs = _store().versions(args.brief_id)
    except BriefNotFound as e:
        print(str(e), file=sys.stderr)
        return EXIT_INVALID
    if args.json:
        _json([v.__dict__ for v in vs])
        return 0
    for v in vs:
        when = v.edited_at.isoformat() if v.edited_at else "-"
        print(f"v{v.version:<4} {when}  sha256:{v.content_hash[:16]}")
    return 0


def cmd_diff(args: argparse.Namespace) -> int:
    from pigtail.briefs.diff import diff_briefs

    store = _store()
    try:
        latest = store.latest_version(args.brief_id)
        b = args.b if args.b is not None else latest
        a = args.a if args.a is not None else max(1, b - 1)
        d = diff_briefs(store.get(args.brief_id, a).brief, store.get(args.brief_id, b).brief)
    except (BriefNotFound, BriefInvalid) as e:
        print(str(e), file=sys.stderr)
        return EXIT_INVALID
    if args.json:
        _json(d.to_dict())
        return 0
    if not d.changes:
        print(f"v{a} and v{b} have the same content")
        return 0
    for c in d.changes:
        if c.kind == "changed":
            print(f"~ {c.path}: {json.dumps(c.old)} -> {json.dumps(c.new)}")
        elif c.kind == "added":
            print(f"+ {c.path}: {json.dumps(c.new)}")
        else:
            print(f"- {c.path}: {json.dumps(c.old)}")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    target = Path(args.target)
    try:
        if target.is_file() or args.target == "-":
            brief = _load(_read_source(args.target), upgrade=args.upgrade_v0)
        else:
            brief = _store().get(args.target).brief
    except BriefInvalid as e:
        if args.json:
            _json({"ok": False, "errors": [p.__dict__ for p in e.problems]})
            return EXIT_INVALID
        return _print_invalid(e)
    except BriefNotFound:
        print(f"{args.target}: no such file or brief", file=sys.stderr)
        return EXIT_INVALID
    if args.json:
        _json({"ok": True, "brief_id": brief.brief_id, "warnings": brief.warnings()})
        return 0
    _warn(brief)
    print(f"valid: {brief.brief_id} (schema {brief.schema_version})")
    return 0


def _last_run_version(settings: Any, brief_id: str) -> int | None:
    """The brief version of the last run that got anywhere (for the reuse plan, R18.4)."""
    if not settings.database_url:
        return None
    import psycopg

    from pigtail.briefs.cache import BriefRuns

    try:
        with psycopg.connect(settings.database_url, connect_timeout=3) as conn:
            return BriefRuns(conn).last_run_version(brief_id)
    except psycopg.Error as e:
        print(
            f"warning: no run history ({type(e).__name__}); estimating a full run",
            file=sys.stderr,
        )
        return None


def cmd_estimate(args: argparse.Namespace) -> int:
    """R18.5 / ADR-053.1: cost estimate before a run; paid steps need explicit approval."""
    from pigtail.briefs.estimate import estimate_for, render_text

    s = _settings()
    store = BriefStore.from_data_dir(s.data_dir)
    try:
        brief = store.get(args.brief_id, args.version).brief
    except (BriefNotFound, BriefInvalid) as e:
        print(str(e), file=sys.stderr)
        return EXIT_INVALID
    est, plan = estimate_for(
        brief,
        store=store,
        data_dir=s.data_dir,
        model=s.llm_model,
        last_run_version=_last_run_version(s, brief.brief_id),
    )
    out = est.to_dict()
    recorded = None
    if est.requires_approval and args.approve_paid and s.database_url:
        import psycopg

        from pigtail.briefs.cache import BriefRuns

        with psycopg.connect(s.database_url, autocommit=True) as conn:
            recorded = (
                BriefRuns(conn)
                .create(brief, data_version=None, estimate=out, approved_paid=True, plan=plan)
                .id
            )
    out["approved_paid"] = bool(args.approve_paid)
    out["planned_run"] = recorded
    if args.json:
        _json(out)
    else:
        print(render_text(est, brief))
        for w in brief.warnings():
            print(f"warning: {w}", file=sys.stderr)
    if est.requires_approval and not args.approve_paid:
        print(
            "\nThis run has paid steps. Nothing was started. Approve the estimate explicitly "
            "with --approve-paid (ADR-053.1, H6).",
            file=sys.stderr,
        )
        return EXIT_NEEDS_APPROVAL
    if est.requires_approval and recorded is None:
        print(
            "warning: approval not recorded (no DATABASE_URL); approve again when you start "
            "the run",
            file=sys.stderr,
        )
    return 0


def cmd_schema(_args: argparse.Namespace) -> int:
    print(json.dumps(json_schema(), indent=2))
    return 0


def add_commands(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    br = sub.add_parser("brief", help="research briefs (PRD F18; private, never in git)")
    bs = br.add_subparsers(dest="brief_command", required=True)

    p = bs.add_parser("new", help="create a brief (version 1) from YAML or the example")
    p.add_argument("--from", dest="source", help="YAML file, or - for stdin")
    p.add_argument("--example", action="store_true", help="start from the synthetic example")
    p.add_argument("--id", help="brief id (overrides the file's brief_id)")
    p.add_argument(
        "--upgrade-v0", action="store_true", help="convert a provisional v0 brief layout"
    )
    p.set_defaults(func=cmd_new)

    p = bs.add_parser("edit", help="save an edit as a new version")
    p.add_argument("brief_id")
    p.add_argument("--from", dest="source", help="YAML file, or - (default: open $EDITOR)")
    p.add_argument("--base-version", type=int, help="the version your edit started from")
    p.set_defaults(func=cmd_edit)

    p = bs.add_parser("show", help="print a brief version (YAML, or --json)")
    p.add_argument("brief_id")
    p.add_argument("--version", type=int)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_show)

    p = bs.add_parser("list", help="every brief in this install")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_list)

    p = bs.add_parser("versions", help="the versions of one brief")
    p.add_argument("brief_id")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_versions)

    p = bs.add_parser("diff", help="field-level diff between two versions")
    p.add_argument("brief_id")
    p.add_argument("a", nargs="?", type=int, help="from version (default: previous)")
    p.add_argument("b", nargs="?", type=int, help="to version (default: latest)")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_diff)

    p = bs.add_parser("validate", help="validate a YAML file or a stored brief")
    p.add_argument("target", help="file path, - for stdin, or a brief id")
    p.add_argument("--upgrade-v0", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_validate)

    p = bs.add_parser("estimate", help="cost estimate before a run (R18.5)")
    p.add_argument("brief_id")
    p.add_argument("--version", type=int)
    p.add_argument(
        "--approve-paid", action="store_true", help="explicitly approve the paid steps shown"
    )
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_estimate)

    bs.add_parser("schema", help="print the brief JSON Schema (v1)").set_defaults(func=cmd_schema)
