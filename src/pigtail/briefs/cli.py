"""`pigtail brief ...` (PRD F18, M12; D7).

    pigtail brief new --from FILE [--id ID] [--upgrade-v0]   create a brief (version 1)
    pigtail brief new --example --id ID                      start from the synthetic example
    pigtail brief edit ID [--from FILE] [--base-version N | --force-latest]
                                                             new version ($EDITOR without --from)
    pigtail brief show ID [--version N] [--json]
    pigtail brief list [--json]
    pigtail brief versions ID [--json]
    pigtail brief diff ID [A [B]] [--json]                   default: previous vs latest
    pigtail brief validate FILE|ID [--upgrade-v0] [--json]
    pigtail brief estimate ID [--version N] [--approve-paid] [--json]
    pigtail brief expand ID [--version N] [--approve-paid] [--out FILE] [--json]
                                                             propose an LLM expansion (R18.7)
    pigtail brief expand ID --accept FILE                    save an (edited) proposal
    pigtail brief expand ID --edit                           propose, edit in $EDITOR, save
    pigtail brief shortlist show ID [--version N] [--verdict V] [--panel P] [--distance D] [--json]
    pigtail brief shortlist accept|reject ID [CANDIDATE ...] --reason R
                            [--verdict V] [--panel P] [--distance D] [--as ROLE]
                                                             review decisions (R4.7), bulk by filter
    pigtail brief shortlist add ID URL --reason R [--panel P] [--resolves named:reference:0]
    pigtail brief shortlist finalize ID [--as ROLE]         mark the shortlist final
    pigtail brief preregister ID --print-hashes [--json]     SHA-256 values to paste into the
                                                             pre-registration (no brief content)
    pigtail brief preregister ID --file PATH [--commit SHA]  record the pre-registration (R8.2)
    pigtail brief selection show ID [--version N] [--json]   winners, losers, balance, sensitivity
    pigtail brief schema                                     print schemas/brief/v1.2.json
    pigtail brief migrate-store --from DIR [--dry-run]       move briefs to PIGTAIL_BRIEFS_DIR

    pigtail run --brief ID [--version N] [--incremental] [--stage S ...] [--dry-run]
                [--approve-paid] [--wait-minutes M] [--json]
                                                             run the brief's stages (R19.1, M22)

`edit` refuses a stale edit: the base version is `--base-version`, else the file's `version:`
field. A file without `version:` is refused unless `--force-latest` says to treat it as an edit
of the latest version (M12 follow-up, ADR-058.2).

Exit codes: 0 ok; 1 invalid brief, not found or stale edit (run: a stage failed; resumable);
2 usage/config error; 3 paid steps not approved (`--approve-paid`); 4 a budget cap (H6), the
GitHub request budget or an LLM limit stopped the work (run: resumable checkpoint), or the
estimate exceeds a cap; 5 (run) a Message Batch is still running: run the same command again to
collect it; 6 (run) another run of the brief is in progress; 7 (run) the selection needs the brief
version's pre-registration first (`pigtail brief preregister`, R8.2): nothing was fetched or
computed.
Briefs are private: they live in PIGTAIL_BRIEFS_DIR (default ~/.pigtail/briefs), outside git,
and are included in the encrypted backup (R18.9, ADR-071.3).
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
EXIT_BUDGET = 4


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
    s = _settings()
    store = BriefStore.from_settings(s)
    if (hint := store.legacy_warning(s.data_dir)) is not None:
        print(f"warning: {hint}", file=sys.stderr)
    return store


def _brief_spent(settings: Any, brief_id: str) -> float:
    """What the brief has already spent (API cost ledger, migration 0020); 0 without a DB."""
    if not settings.database_url:
        return 0.0
    import psycopg

    from pigtail.llm.batch import PgCostLedger

    try:
        with psycopg.connect(settings.database_url, connect_timeout=3) as conn:
            return PgCostLedger(conn).brief_total(brief_id)
    except psycopg.Error as e:
        print(
            f"warning: no cost ledger ({type(e).__name__}); counting this brief's past spend as 0",
            file=sys.stderr,
        )
        return 0.0


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


STALE_HINT = (
    "Your file is based on an older version, and saving it would silently revert the newer "
    "changes. Export the latest (`pigtail brief show ID > file`), re-apply your edit, and save "
    "again; or pass --base-version N if you really mean to replace the latest version."
)


class MissingVersion(ValueError):
    """An edit file without `version:` and without `--force-latest`/`--base-version`."""


def edit_base_version(
    *,
    explicit: int | None,
    file_version: int | None,
    latest: int,
    from_editor: bool,
    force_latest: bool = False,
) -> int:
    """The version an edit is based on (stale edits are refused against it).

    `--base-version` wins; an edit opened in $EDITOR is based on the version it loaded; a file
    is based on its own `version:` field. A file without one is refused (`MissingVersion`)
    unless `force_latest` says to treat it as an edit of the latest version.
    """
    if explicit is not None:
        if file_version is not None and file_version != explicit:
            print(
                f"warning: the file says version {file_version}; using --base-version {explicit}",
                file=sys.stderr,
            )
        return explicit
    if from_editor:
        return latest
    if file_version is not None:
        return file_version
    if not force_latest:
        raise MissingVersion(
            "the file has no `version:` field, so pigtail can't tell which version it edits "
            "(an old export would silently revert newer changes). Keep the `version:` line from "
            f"`pigtail brief show`, pass --base-version N, or --force-latest to treat it as an "
            f"edit of the latest version (v{latest})"
        )
    print(
        f"warning: --force-latest: the file has no `version:` field and is treated as an edit "
        f"of the latest version (v{latest})",
        file=sys.stderr,
    )
    return latest


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
        base = edit_base_version(
            explicit=args.base_version,
            file_version=brief.version,
            latest=latest.version,
            from_editor=not args.source,
            force_latest=args.force_latest,
        )
        stored, created = store.save_version(brief, base_version=base)
    except MissingVersion as e:
        print(f"refused, nothing saved: {e}", file=sys.stderr)
        return EXIT_INVALID
    except BriefInvalid as e:
        return _print_invalid(e)
    except VersionConflict as e:
        print(f"refused, nothing saved: {e}. {STALE_HINT}", file=sys.stderr)
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
    store = _store()
    try:
        brief = store.get(args.brief_id, args.version).brief
    except (BriefNotFound, BriefInvalid) as e:
        print(str(e), file=sys.stderr)
        return EXIT_INVALID
    est, plan = estimate_for(
        brief,
        store=store,
        data_dir=s.data_dir,
        models=dict(s.llm_models),
        batch=s.llm_batch,
        month_cap_usd=s.budget_usd_month,
        last_run_version=_last_run_version(s, brief.brief_id),
        brief_spent_usd=_brief_spent(s, brief.brief_id),
    )
    out = est.to_dict()
    recorded = None
    over_cap = out["caps"]["within_caps"] is False
    if over_cap:
        print(
            "\nThe estimate exceeds a cap (see above): the run would hard-stop there. Spending "
            "above budget.money_usd or BUDGET_USD_MONTH needs the owner's approval (H6); lower "
            "the scope or raise the cap first.",
            file=sys.stderr,
        )
    if est.requires_approval and args.approve_paid and over_cap:
        print("approval not recorded: the estimate exceeds a cap (H6)", file=sys.stderr)
        return EXIT_BUDGET
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


def _llm_client() -> Any:
    """The product LLM client (PRD F15). Tests replace this with a fake backend."""
    from pigtail.llm import build_client

    return build_client(_settings())


def _write_private(path: str, text: str) -> None:
    if path == "-":
        sys.stdout.write(text)
        return
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)


def _proposal_yaml(proposal: dict[str, Any]) -> str:
    import yaml

    header = (
        "# LLM expansion PROPOSAL (PRD R18.7). Nothing is saved yet. Edit the `expansion` fields,\n"
        "# then accept: pigtail brief expand {bid} --accept <this file>\n"
        "# Unverified model output: check every competitor and URL. Keep this file out of git.\n"
    ).format(bid=proposal["brief_id"])
    body = {
        "brief_id": proposal["brief_id"],
        "base_version": proposal["base_version"],
        "notes": proposal["notes"],
        "expansion": proposal["expansion"],
    }
    return header + yaml.safe_dump(body, sort_keys=False, allow_unicode=True, width=100)


def _accept(store: BriefStore, brief_id: str, text: str) -> int:
    from pigtail.briefs.expansion import apply_expansion

    try:
        doc = parse_yaml(text)
    except BriefInvalid as e:
        return _print_invalid(e)
    if not isinstance(doc, dict) or "expansion" not in doc:
        print("the file has no `expansion:` block (use the file `--out` wrote)", file=sys.stderr)
        return EXIT_INVALID
    if doc.get("brief_id", brief_id) != brief_id:
        print(f"the proposal is for {doc.get('brief_id')!r}, not {brief_id!r}", file=sys.stderr)
        return EXIT_INVALID
    base = doc.get("base_version")
    if not isinstance(base, int) or isinstance(base, bool):
        print("the proposal has no `base_version:`; propose again", file=sys.stderr)
        return EXIT_INVALID
    try:
        stored, created = apply_expansion(store, brief_id, doc["expansion"], base_version=base)
    except BriefInvalid as e:
        return _print_invalid(e)
    except BriefNotFound as e:
        print(str(e), file=sys.stderr)
        return EXIT_INVALID
    except VersionConflict as e:
        print(
            f"refused, nothing saved: {e}. The brief changed after this proposal; propose again "
            "(or copy your edits into a new proposal).",
            file=sys.stderr,
        )
        return EXIT_INVALID
    _warn(stored.brief, store)
    exp = stored.brief.expansion
    edited = bool(exp and exp.provenance and exp.provenance.edited_by_user)
    if created:
        print(
            f"saved {brief_id} v{stored.version} with the expansion"
            + (" (edited by you)" if edited else "")
        )
    else:
        print(f"no changes; {brief_id} stays at v{stored.version}")
    return 0


def cmd_expand(args: argparse.Namespace) -> int:
    """R18.7: propose an LLM expansion; save it only when the user accepts it."""
    from pigtail.briefs.budget import BudgetStop
    from pigtail.briefs.expansion import make_guard, propose_expansion
    from pigtail.llm import LLMError

    store = _store()
    if args.accept is not None:
        if args.edit or args.out:
            print("--accept can't be combined with --edit or --out", file=sys.stderr)
            return EXIT_USAGE
        return _accept(store, args.brief_id, _read_source(args.accept))
    try:
        brief = store.get(args.brief_id, args.version).brief
    except (BriefNotFound, BriefInvalid) as e:
        print(str(e), file=sys.stderr)
        return EXIT_INVALID
    try:
        client = _llm_client()
    except ValueError as e:
        print(f"LLM client not configured: {e}", file=sys.stderr)
        return EXIT_USAGE
    try:
        s = _settings()
        guard = make_guard(
            client,
            brief,
            approved_paid=args.approve_paid,
            month_cap_usd=s.budget_usd_month,
            spent_usd=_brief_spent(s, brief.brief_id),
        )
        proposal = propose_expansion(brief, client, guard)
    except BudgetStop as e:
        print(f"nothing was sent to the model: {e}", file=sys.stderr)
        return EXIT_NEEDS_APPROVAL if e.kind == "approval" else EXIT_BUDGET
    except LLMError as e:
        print(f"expansion failed, nothing saved: {type(e).__name__}: {e}", file=sys.stderr)
        return EXIT_BUDGET
    out = proposal.to_dict()
    if args.edit:
        edited = _edit_in_editor(_proposal_yaml(out), store)
        if edited is None:
            return EXIT_USAGE
        return _accept(store, args.brief_id, edited)
    if args.json and not args.out:
        _json(out)
    else:
        _write_private(args.out or "-", _proposal_yaml(out))
        if args.out:
            print(
                f"proposal written to {args.out} (not saved). Edit it, then: "
                f"pigtail brief expand {brief.brief_id} --accept {args.out}",
                file=sys.stderr,
            )
    return 0


def cmd_schema(_args: argparse.Namespace) -> int:
    print(json.dumps(json_schema(), indent=2))
    return 0


def cmd_migrate_store(args: argparse.Namespace) -> int:
    """ADR-071.3 / R18.9: move briefs once from an old store (e.g. PIGTAIL_DATA_DIR/briefs) to
    PIGTAIL_BRIEFS_DIR. Moves bytes; never reads or prints brief content (counts only)."""
    from pigtail.briefs.store import migrate_store

    s = _settings()
    target = Path(args.to).expanduser() if args.to else s.briefs_dir
    try:
        rep = migrate_store(Path(args.source), target, dry_run=args.dry_run)
    except (BriefNotFound, ValueError) as e:
        print(str(e), file=sys.stderr)
        return EXIT_USAGE
    out = rep.to_dict()
    if not args.verbose:
        out.pop("conflict_paths")
    print(json.dumps(out, indent=2))
    if (w := BriefStore(target).exposure_warning()) is not None:
        print(f"warning: {w}", file=sys.stderr)
    if rep.conflicts:
        print(
            f"{rep.conflicts} version file(s) differ between the two stores and were left in "
            "place in both; compare them (--verbose lists their paths) before deleting either",
            file=sys.stderr,
        )
        return EXIT_INVALID
    return 0


# --- pigtail run (R19.1, M22) -----------------------------------------------------------------


def _discovery_config() -> Any:
    from pigtail.briefs.discovery import DiscoveryConfig

    raw = (os.environ.get("PIGTAIL_DISCOVERY_GHARCHIVE_HOURS") or "").strip()
    hours = int(raw) if raw else 0
    if not 0 <= hours <= 48:
        raise ValueError("PIGTAIL_DISCOVERY_GHARCHIVE_HOURS must be 0..48 (0: off)")
    return DiscoveryConfig(gharchive_hours=hours)


def _connectors(s: Any, db: Any, recorder: Any, need_github: bool) -> tuple[Any, Any, Any]:
    """GitHub (with the per-hour request budget and a per-run cap), Show HN, GH Archive."""
    from pigtail.capture.snapshots import build_store
    from pigtail.connectors.github import TOKEN_ENV, GitHubConnector, PostgresCache
    from pigtail.connectors.github_budget import Budget, BudgetConfig, JobCaps, PostgresLedger
    from pigtail.connectors.hn import HNShowDiscoveryConnector
    from pigtail.privacy import suppression

    snaps = build_store(s)
    github = None
    if (os.environ.get(TOKEN_ENV) or "").strip():
        budget = Budget(
            BudgetConfig.from_env(os.environ),
            PostgresLedger(db.conn),
            job=JobCaps({"search": 600, "graphql": 1000, "core": 4000}),
        )
        github = GitHubConnector(
            store=snaps,
            pseudonymizer=None,
            run=recorder,
            evidence_sink=db.upsert_evidence,
            suppression=suppression.load(db),
            budget=budget,
            cache=PostgresCache(db.conn),
        )
    elif need_github:
        raise ValueError(f"{TOKEN_ENV} is not set: discovery makes no GitHub call without it")
    hn = None
    if HNShowDiscoveryConnector.enabled_from_env(os.environ):
        hn = HNShowDiscoveryConnector(
            store=snaps, pseudonymizer=None, run=recorder, evidence_sink=db.upsert_evidence
        )
    gharchive = None
    if _discovery_config().gharchive_hours > 0:
        from pigtail.connectors.gharchive import GHArchiveConnector
        from pigtail.pseudonymize import OptoutKey, optout_key_from_env

        key = optout_key_from_env()
        if key:
            gharchive = GHArchiveConnector(
                store=snaps,
                pseudonymizer=OptoutKey(key),
                run=recorder,
                evidence_sink=db.upsert_evidence,
                suppression=suppression.load(db),
            )
    return github, hn, gharchive


def cmd_run(args: argparse.Namespace) -> int:
    """R19.1: run (or resume) a brief's stages; the cost estimate is shown first (R18.5)."""
    from pigtail.briefs.estimate import (
        RUN_STAGES,
        estimate_for,
        render_scope_text,
        render_text,
        run_scope,
    )
    from pigtail.briefs.runner import RunDeps, RunOptions, find_run, run_brief

    s = _settings()
    store = _store()
    try:
        brief = store.get(args.brief, args.version).brief
    except (BriefNotFound, BriefInvalid) as e:
        print(str(e), file=sys.stderr)
        return EXIT_INVALID
    stages = tuple(args.stage) if args.stage else RUN_STAGES
    est, _plan = estimate_for(
        brief,
        store=store,
        data_dir=s.data_dir,
        models=dict(s.llm_models),
        batch=s.llm_batch,
        month_cap_usd=s.budget_usd_month,
        last_run_version=_last_run_version(s, brief.brief_id),
        brief_spent_usd=_brief_spent(s, brief.brief_id),
    )
    scope = run_scope(est, stages)
    out: dict[str, Any] = {"estimate": est.to_dict(), "run_scope": scope}
    if not args.json:
        print(render_text(est, brief))
        print()
        print(render_scope_text(scope))
    for w in brief.warnings():
        print(f"warning: {w}", file=sys.stderr)
    if not s.database_url:
        print("DATABASE_URL is not set: runs keep their checkpoints in Postgres", file=sys.stderr)
        return EXIT_USAGE
    import psycopg

    from pigtail.db.migrate import migrate

    if args.dry_run:
        try:
            with psycopg.connect(s.database_url, connect_timeout=3) as conn:
                kind, row = find_run(conn, brief)
        except psycopg.Error as e:
            kind, row = f"unknown ({type(e).__name__})", None
        out["dry_run"] = {
            "would": kind,
            "run": row["id"] if row else None,
            "stages": list(stages),
            "incremental": args.incremental,
            "network_calls": 0,
            "writes": 0,
        }
        if args.json:
            _json(out)
        else:
            what = {"new": "start a new run", "resume": "resume run", "complete": "do nothing"}
            print(
                f"\nDRY RUN: nothing was started. Would {what.get(kind, kind)}"
                + (f" {row['id']}" if row else "")
                + (" (--incremental: refresh)" if args.incremental and kind == "complete" else "")
                + "."
            )
        return 0
    migrate(s.database_url)
    conn = psycopg.connect(s.database_url, autocommit=True)
    try:
        kind, row = find_run(conn, brief)
        approved = args.approve_paid or bool(row and kind == "resume" and row.get("approved_paid"))
        if scope["requires_approval"] and not approved:
            print(
                "\nThis run has paid steps (LLM calls on the api backend). Nothing was started. "
                "Approve the estimate explicitly with --approve-paid (ADR-053.1, H6).",
                file=sys.stderr,
            )
            return EXIT_NEEDS_APPROVAL
        if scope["within_caps"] is False:
            print(
                "warning: the estimate exceeds a cap; the run will hard-stop there with a "
                "resumable checkpoint (H6)",
                file=sys.stderr,
            )
        from pigtail.capture.db import CaptureDB
        from pigtail.capture.runs import RunRecorder

        db = CaptureDB(conn)
        try:
            client = _llm_client()
        except ValueError as e:
            print(f"LLM client not configured: {e}", file=sys.stderr)
            return EXIT_USAGE
        config = {
            "brief_id": brief.brief_id,
            "brief_version": brief.version,
            "stages": list(stages),
            "incremental": args.incremental,
        }
        with RunRecorder("brief.run", config, sink=db.upsert_run) as rec:
            try:
                github, hn, gha = _connectors(s, db, rec, need_github="discovery" in stages)
                opts = RunOptions(
                    stages=stages,
                    incremental=args.incremental,
                    approve_paid=args.approve_paid,
                    wait_seconds=args.wait_minutes * 60 if args.wait_minutes is not None else None,
                    discovery=_discovery_config(),
                    estimate=out,
                )
            except ValueError as e:
                print(str(e), file=sys.stderr)
                return EXIT_USAGE
            deps = RunDeps(
                conn=conn,
                client=client,
                github=github,
                hn=hn,
                gharchive=gha,
                month_cap_usd=s.budget_usd_month,
                run_record_id=rec.id,
                recorder=rec,
            )
            outcome = run_brief(brief, deps, opts)
    finally:
        conn.close()
    out["outcome"] = outcome.to_dict()
    if args.json:
        _json(out)
    else:
        print(f"\n{outcome.status}: {outcome.message}")
        if outcome.brief_run_id:
            print(f"brief run: {outcome.brief_run_id}")
    if outcome.exit_code not in (0,):
        print(outcome.message, file=sys.stderr)
    return outcome.exit_code


def add_run_command(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    from pigtail.briefs.estimate import RUN_STAGES

    p = sub.add_parser("run", help="run (or resume) a brief's stages (R19.1)")
    p.add_argument("--brief", required=True, help="brief id")
    p.add_argument("--version", type=int, help="brief version (default: latest)")
    p.add_argument("--incremental", action="store_true", help="refresh a completed run")
    p.add_argument(
        "--stage", action="append", choices=RUN_STAGES, help="run only these stages (repeatable)"
    )
    p.add_argument("--dry-run", action="store_true", help="show the estimate and plan; do nothing")
    p.add_argument("--approve-paid", action="store_true", help="approve the paid steps shown")
    p.add_argument(
        "--wait-minutes",
        type=float,
        help="poll a Message Batch this long, then leave it running (default: until it ends)",
    )
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_run)


# --- pigtail brief shortlist (R4.7, D7) -----------------------------------------------------


def _shortlist(args: argparse.Namespace) -> tuple[Any, Any] | int:
    import psycopg

    from pigtail.briefs.shortlist import Shortlist

    s = _settings()
    if not s.database_url:
        print("DATABASE_URL is not set", file=sys.stderr)
        return EXIT_USAGE
    try:
        brief = _store().get(args.brief_id, args.version).brief
    except (BriefNotFound, BriefInvalid) as e:
        print(str(e), file=sys.stderr)
        return EXIT_INVALID
    conn = psycopg.connect(s.database_url, autocommit=True)
    return conn, Shortlist(conn, brief)


def _print_view(v: dict[str, Any]) -> None:
    p = v["precision"]
    c = v["counts"]
    print(
        f"Shortlist {v['brief_id']} v{v['brief_version']}: {v['status'] or 'not started'}; "
        f"{c['candidates']} candidates {c['by_verdict']}, on the shortlist {c['on_shortlist']}, "
        f"proposed {c['proposed']}, undecided {c['undecided']}"
    )
    val = "n/a" if p["value"] is None else f"{p['value'] * 100:.0f} %"
    print(
        f"Precision {val} ({p['kept']}/{p['decided']} of {p['model_relevant']} model-relevant; "
        f"target {p['target'] * 100:.0f} %; {p['label']})"
    )
    # R4.7 / D7: the review is where defaulted brief fields are confirmed (ADR-062)
    if v.get("defaulted_fields"):
        print(f"\nBrief fields to confirm ({len(v['defaulted_fields'])} still at their default):")
        for f in v["defaulted_fields"]:
            print(f"  {f['field']}: {json.dumps(f['value'], default=str)}")
    for w in v.get("brief_warnings") or []:
        print(f"brief warning: {w}")
    if v.get("defaulted_fields") or v.get("brief_warnings"):
        print()
    for r in v["candidates"]:
        d = r["decision"]
        mark = "+" if r["on_shortlist"] else ("?" if r["on_shortlist"] is None else "-")
        dist = "" if r["distance"] is None else f" d{r['distance']}"
        print(
            f"{mark} {r['candidate_ref']:<45} {r['panel']:<9} {r['verdict'] or 'not judged':<12}"
            f"{dist:<4} {r['stars'] if r['stars'] is not None else '':>7}  {r['reason'] or ''}"
            + (f"  [{d['decision']}: {d['reason']}]" if d else "")
        )
    if v["reference_cases_to_confirm"]:
        print("\nReference cases to confirm (add <url> --resolves <ref>):")
        for r in v["reference_cases_to_confirm"]:
            print(f"  {r['candidate_ref']} ({r.get('named_as') or '?'}): {r['resolution_rule']}")
            for m in r["matches"]:
                print(f"    {m['url']}  {m.get('stars') or ''}  {m.get('description') or ''}")


def cmd_shortlist(args: argparse.Namespace) -> int:
    from pigtail.briefs.candidates import BadCandidate
    from pigtail.briefs.shortlist import ShortlistError

    got = _shortlist(args)
    if isinstance(got, int):
        return got
    conn, sl = got
    try:
        action = args.shortlist_command
        if action == "show":
            v = sl.view(verdict=args.verdict, panel=args.panel, distance=args.distance)
            if args.json:
                _json(v)
            else:
                _print_view(v)
            return 0
        if action in ("accept", "reject"):
            if args.candidates:
                n = sl.decide(args.candidates, action, args.reason, reviewer=args.role, via="cli")
            elif args.verdict or args.panel or args.distance is not None:
                n = sl.decide_where(
                    action,
                    args.reason,
                    verdict=args.verdict,
                    panel=args.panel,
                    distance=args.distance,
                    reviewer=args.role,
                    via="cli",
                )
            else:
                print(
                    "give candidates, or a filter (--verdict/--panel/--distance)", file=sys.stderr
                )
                return EXIT_USAGE
            print(f"{action}: {n} decision(s) logged")
            return 0
        if action == "add":
            ref = sl.add(
                args.url,
                args.reason,
                panel=args.panel or "field",
                resolves=args.resolves,
                reviewer=args.role,
                via="cli",
            )
            print(f"added {ref} (metadata is filled by the next run --incremental)")
            return 0
        if action == "finalize":
            res = sl.finalize(reviewer=args.role, via="cli")
            p = res["precision"]
            val = "n/a" if p["value"] is None else f"{p['value'] * 100:.0f} %"
            print(
                f"final: {res['shortlisted']} repos on the shortlist; precision {val} "
                f"({p['label']}; target {p['target'] * 100:.0f} %"
                + ("; BELOW TARGET" if p["meets_target"] is False else "")
                + ")"
            )
            if res["unresolved_named"]:
                print(f"unresolved named projects (not blocking): {len(res['unresolved_named'])}")
            return 0
    except (ShortlistError, BadCandidate) as e:
        print(str(e), file=sys.stderr)
        return EXIT_INVALID
    finally:
        conn.close()
    return EXIT_USAGE


def _add_shortlist_commands(bs: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    sp = bs.add_parser("shortlist", help="shortlist review (R4.7): show, decide, add, finalize")
    ss = sp.add_subparsers(dest="shortlist_command", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("brief_id")
        p.add_argument("--version", type=int, help="brief version (default: latest)")

    def filters(p: argparse.ArgumentParser) -> None:
        p.add_argument("--verdict", choices=["relevant", "not_relevant", "uncertain", "none"])
        p.add_argument("--panel", choices=["field", "exemplar", "reference"])
        p.add_argument("--distance", type=int, choices=[0, 1, 2])

    def role(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--as",
            dest="role",
            choices=["user", "owner", "verifier"],
            default="user",
            help="reviewer role logged with the decision (default: user)",
        )

    p = ss.add_parser("show", help="candidates, verdicts, reasons, decisions and precision")
    common(p)
    filters(p)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_shortlist)
    for action in ("accept", "reject"):
        p = ss.add_parser(action, help=f"{action} candidates (or all matching a filter)")
        common(p)
        p.add_argument("candidates", nargs="*", help="owner/name, URL or candidate ref")
        p.add_argument("--reason", required=True, help="why (logged with the decision)")
        filters(p)
        role(p)
        p.set_defaults(func=cmd_shortlist)
    p = ss.add_parser("add", help="add a repo the filter missed, by URL")
    common(p)
    p.add_argument("url", help="https://github.com/owner/name or owner/name")
    p.add_argument("--reason", required=True)
    p.add_argument("--panel", choices=["field", "exemplar", "reference"])
    p.add_argument("--resolves", help="the unresolved named project this repo stands for")
    role(p)
    p.set_defaults(func=cmd_shortlist)
    p = ss.add_parser("finalize", help="mark the shortlist final (mention scope: final)")
    common(p)
    role(p)
    p.set_defaults(func=cmd_shortlist)


# --- pigtail brief selection (R4.3, R4.8, R4.9; ADR-077) ---------------------------------------


def _fmt(x: Any, nd: int = 2) -> str:
    return "n/a" if x is None else f"{x:.{nd}f}"


def _print_selection(v: dict[str, Any]) -> None:
    sel = v["selection"]
    sm, bal, sens = sel["summary"], sel["balance"], sel["sensitivity"]
    print(
        f"Selection {sel['id']} of {v['brief_id']} v{v['brief_version']} "
        f"(as of {sel['as_of']}, data {sel['data_version']}, {sel['selection_version']}, "
        f"code {sel['code_commit'] or 'unknown'})"
    )
    print(f"  result hash {sel['result_hash'][:16]}…, inputs hash {sel['inputs_hash'][:16]}…")
    c = sm["counts"]
    print(
        f"Shortlist {sm['shortlist_n']}; reference population {sm['reference_population_n']}; "
        f"final distance {sm['final_distance']}; winners {c['winners']}, matched losers "
        f"{c['matched_losers']}, loser pool {c['loser_pool']}, undetermined {c['undetermined']}"
    )
    print("Roles: " + ", ".join(f"{k} {n}" for k, n in sm["roles"].items()))
    print("Steps (R4.10, ADR-053.2):")
    for st in sm["steps"]:
        if st.get("applied") is False:
            print(f"  {st['step']}: not applied ({st['reason']})")
            continue
        print(
            f"  {st['step']:<28} d{st['distance']} floors {st['floors']} -> qualifiers "
            f"{st['rankable_qualifiers']}, winners {st['winners']}, matched losers "
            f"{st['matched_losers']}" + (f"  ({st['reason']})" if st.get("reason") else "")
        )
    und = {k: n for k, n in sm["undetermined_by_dimension"].items() if n}
    if und:
        print(f"Undetermined by dimension (unknown/pending never meet a threshold): {und}")
    for w in sm["warnings"]:
        print(f"warning: {w}")
    ex = bal["exact_match"]
    print(
        f"Balance ({bal['form']}; target |SMD| < {bal['target']}): exact match "
        f"{'OK' if ex['ok'] else 'VIOLATED ' + str(ex['violations'])} on {ex['keys']}; "
        f"headline pairs {bal['headline_pairs']}/{bal['pairs']}, excluded "
        f"{bal['headline_excluded']['by_covariate'] or 0}"
    )
    for cov, rec in bal["after_matching"].items():
        before = bal["before_matching"].get(cov, {}).get("smd")
        print(
            f"  {cov:<16} SMD {_fmt(rec['smd'])} (before {_fmt(before)})"
            + (
                f", variance ratio {_fmt(rec.get('variance_ratio'))}"
                if "variance_ratio" in rec
                else ""
            )
            + (" balance_limited" if rec.get("label") else "")
        )
    print(
        f"Sensitivity (R4.9): {sens['ran']} alternatives, Jaccard min {_fmt(sens['min_jaccard'])}"
        f" mean {_fmt(sens['mean_jaccard'])}; winners stable under all "
        f"{_fmt(sens['share_winners_stable'])}; definition-sensitive "
        f"{sens['definition_sensitive']}, sensitive to star anomaly "
        f"{sens['sensitive_to_star_anomaly']}"
    )
    for alt in sens["alternatives"]:
        if alt.get("ran"):
            print(
                f"  {alt['key']:<32} qualifiers {alt['rankable_qualifiers']}, winners "
                f"{alt['winners']}, Jaccard {_fmt(alt['jaccard'])}"
            )
        else:
            print(f"  {alt['key']:<32} not run: {alt['reason']}")
    print("Cases (pair, role, rank, repo, anchor, star anomaly, flags):")
    for r in v["cases"]:
        d = r["detail"]
        a = d.get("anchor") or {}
        pair = (
            ""
            if r["pair_id"] is None
            else f"#{r['pair_id']}{'' if r['headline'] is not False else '*'}"
        )
        print(
            f"  {pair:<5} {r['role']:<24} {r['rank'] or '':>3} {r['repo_full_name']:<40} "
            f"{a.get('type', '-'):<6} {d['star_anomaly']['flag']:<7} "
            + ("reference " if r["is_reference"] else "")
            + " ".join(r["sensitivity_flags"])
        )
    print("  (* = excluded from headline patterns; star metrics: unfiltered, anomaly-checked)")


def cmd_selection(args: argparse.Namespace) -> int:
    import psycopg

    from pigtail.briefs.selection_store import view

    s = _settings()
    if not s.database_url:
        print("DATABASE_URL is not set", file=sys.stderr)
        return EXIT_USAGE
    try:
        brief = _store().get(args.brief_id, args.version).brief
    except (BriefNotFound, BriefInvalid) as e:
        print(str(e), file=sys.stderr)
        return EXIT_INVALID
    assert brief.version is not None
    with psycopg.connect(s.database_url, autocommit=True) as conn:
        v = view(conn, brief.brief_id, brief.version)
    if args.json:
        _json(v)
        return 0
    if v["selection"] is None:
        print(
            f"no selection for {brief.brief_id} v{brief.version} yet: finalize the shortlist, "
            f"pre-register (pigtail brief preregister {brief.brief_id}), then pigtail run "
            f"--brief {brief.brief_id} --stage selection"
        )
        return EXIT_INVALID
    _print_selection(v)
    return 0


# --- pigtail brief preregister (R8.2; ADR-065, ADR-078) ----------------------------------------


def cmd_preregister(args: argparse.Namespace) -> int:
    from pigtail.briefs.preregistration import TEMPLATE, PreregistrationError, hashes, record

    try:
        brief = _store().get(args.brief_id, args.version).brief
    except (BriefNotFound, BriefInvalid) as e:
        print(str(e), file=sys.stderr)
        return EXIT_INVALID
    if args.print_hashes:
        h = hashes(brief)
        if args.json:
            _json(h)
            return 0
        print(f"Paste these into your pre-registration ({TEMPLATE}); they reveal no brief text:")
        for k, v in h.items():
            print(f"  {k}: {v}")
        return 0
    if not args.file:
        print("give --file PATH (or --print-hashes)", file=sys.stderr)
        return EXIT_USAGE
    import psycopg

    s = _settings()
    if not s.database_url:
        print("DATABASE_URL is not set", file=sys.stderr)
        return EXIT_USAGE
    try:
        with psycopg.connect(s.database_url, autocommit=True) as conn:
            rec = record(conn, brief, Path(args.file), commit=args.commit)
    except PreregistrationError as e:
        print(str(e), file=sys.stderr)
        return EXIT_INVALID
    if args.json:
        _json(rec.to_dict())
        return 0
    print(
        f"pre-registration recorded for {brief.brief_id} v{brief.version}: {rec.file_path} "
        f"(sha256 {rec.file_sha256[:16]}…, commit {rec.git_commit or 'not given'}); the "
        f"selection may run now (pigtail run --brief {brief.brief_id})"
    )
    return 0


def _add_preregister_command(bs: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p = bs.add_parser(
        "preregister",
        help="record the brief version's pre-registration before its outcome sort (R8.2)",
    )
    p.add_argument("brief_id")
    p.add_argument("--version", type=int, help="brief version (default: latest)")
    p.add_argument("--file", help="the pre-registration file (docs/preregistration/...)")
    p.add_argument("--commit", help="the git commit that holds the file (pushed)")
    p.add_argument(
        "--print-hashes",
        action="store_true",
        help="print the SHA-256 values to paste into the file; record nothing",
    )
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_preregister)


def _add_selection_commands(bs: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    sp = bs.add_parser(
        "selection", help="winners, matched losers, balance, sensitivity (R4.8, R4.3, R4.9)"
    )
    ss = sp.add_subparsers(dest="selection_command", required=True)
    p = ss.add_parser("show", help="the latest stored selection of a brief version")
    p.add_argument("brief_id")
    p.add_argument("--version", type=int, help="brief version (default: latest)")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_selection)


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
    g = p.add_mutually_exclusive_group()
    g.add_argument("--base-version", type=int, help="the version your edit started from")
    g.add_argument(
        "--force-latest",
        action="store_true",
        help="accept a file without `version:` as an edit of the latest version",
    )
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

    p = bs.add_parser("expand", help="propose an LLM expansion; saved only when you accept it")
    p.add_argument("brief_id")
    p.add_argument("--version", type=int, help="brief version to expand (default: latest)")
    p.add_argument(
        "--approve-paid", action="store_true", help="approve the call's cost on the api backend"
    )
    p.add_argument("--out", help="write the proposal (YAML) to this file instead of stdout")
    p.add_argument("--json", action="store_true", help="print the proposal as JSON")
    p.add_argument("--accept", metavar="FILE", help="save an (edited) proposal file, or -")
    p.add_argument("--edit", action="store_true", help="edit the proposal in $EDITOR, then save")
    p.set_defaults(func=cmd_expand)

    _add_shortlist_commands(bs)
    _add_preregister_command(bs)
    _add_selection_commands(bs)

    bs.add_parser("schema", help="print the brief JSON Schema (v1.2)").set_defaults(func=cmd_schema)

    p = bs.add_parser(
        "migrate-store", help="move briefs once from an old store to PIGTAIL_BRIEFS_DIR"
    )
    p.add_argument("--from", dest="source", required=True, help="old store, e.g. data/briefs")
    p.add_argument("--to", help="target store (default: PIGTAIL_BRIEFS_DIR)")
    p.add_argument("--dry-run", action="store_true", help="count what would move; move nothing")
    p.add_argument("--verbose", action="store_true", help="list conflicting version paths")
    p.set_defaults(func=cmd_migrate_store)
