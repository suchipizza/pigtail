"""D7 `/briefs` API (PRD F18: R18.1–R18.5; M12). Behind the operator login.

    GET  /api/briefs                          every brief: latest version, status, last run, budget
    POST /api/briefs                          create (form JSON `brief` or `yaml`); 201
    POST /api/briefs/validate                 validate without saving (live form checks)
    GET  /api/brief-template                  the synthetic example as a starting point
    GET  /api/briefs/{id}                     latest version (JSON and YAML), versions, warnings
    GET  /api/briefs/{id}/versions/{n}        one version
    POST /api/briefs/{id}/versions            save an edit as a new version (see "Stale edits")
    POST /api/briefs/{id}/expansion           propose an LLM expansion (R18.7); nothing is saved
    GET  /api/briefs/{id}/diff?from=&to=      field-level and unified diff
    GET  /api/briefs/{id}/estimate?version=   cost estimate (label: estimate)
    GET  /api/briefs/{id}/shortlist?version=&verdict=&panel=&distance=
                                              shortlist review (R4.7): candidates, verdicts,
                                              reasons, decisions, precision, references to confirm
    POST /api/briefs/{id}/shortlist/decisions accept/reject candidates (or by filter), with reason
    POST /api/briefs/{id}/shortlist/add       add a repo by URL (optionally resolving a reference)
    POST /api/briefs/{id}/shortlist/finalize  mark the shortlist final

Shortlist writes (M22, D7) use the write pool, are same-origin JSON POSTs like every other write,
log each decision in `shortlist_decision` (reviewer role `owner`: the web app's one login is the
instance operator, who owns its briefs, ADR-072.8; `via: ui`; time) and are audited by event
only (`shortlist_decision`, `shortlist_finalize`: no names, no reasons).

Writes are POSTs with a same-origin JSON body, exactly like the login (CSRF: JSON content type
required, `Sec-Fetch-Site`/`Origin` checked, SameSite=Strict session cookie) and are audited
by event only (`brief_create`, `brief_version`), never with brief content. The form and YAML are
two views of one model: both are validated by `pigtail.briefs.model.validate_brief` and stored
as the same canonical YAML (R18.2).

Stale edits: a save is based on `base_version`, else on the payload's own `version` field (a
brief exported from version N says `version: N`). If both are given they must agree. A save
with neither is refused (409) unless it sets `force_latest: true`. A base older than the latest
version is refused (409), so an old export can't silently revert newer changes.

Expansion (R18.7): `POST /api/briefs/{id}/expansion` sends only the brief's description, target
users, business model and field include/exclude to the model through `LLMClient` (job
`brief_expansion`), after `BudgetGuard` checks (no automatic backend switch, ADR-053.1). It
returns a proposal and saves nothing; the user edits it in `/briefs` and saving the brief
stores it with its provenance (a normal new version, audited as `brief_version`). Calls are
audited as `brief_expansion` (event only, no content).
"""

# No `from __future__ import annotations`: FastAPI resolves the dependency annotations.
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Annotated, Any

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from pigtail.briefs.cli import example_path
from pigtail.briefs.diff import diff_briefs
from pigtail.briefs.estimate import estimate_for, selection_state
from pigtail.briefs.model import (
    Brief,
    BriefInvalid,
    BriefProblem,
    dump_yaml,
    parse_yaml,
    validate_brief,
)
from pigtail.briefs.store import (
    BriefExists,
    BriefNotFound,
    BriefStore,
    StoredBrief,
    VersionConflict,
    check_id,
)

MAX_YAML = 200_000


class BriefBody(BaseModel):
    """Exactly one of `brief` (the guided form's JSON) or `yaml` (import)."""

    brief: dict[str, Any] | None = None
    yaml: str | None = Field(default=None, max_length=MAX_YAML)
    base_version: int | None = Field(default=None, ge=1)
    # Save over the latest version when neither `base_version` nor the payload's `version` says
    # which version the edit started from (explicit opt-in; otherwise 409).
    force_latest: bool = False


class DecisionBody(BaseModel):
    version: int | None = Field(default=None, ge=1)
    decision: str = Field(pattern="^(accept|reject)$")
    reason: str = Field(min_length=1, max_length=1000)
    candidates: list[str] = Field(default_factory=list, max_length=2000)
    # bulk by filter when `candidates` is empty (undecided candidates only)
    verdict: str | None = Field(default=None, pattern="^(relevant|not_relevant|uncertain|none)$")
    panel: str | None = Field(default=None, pattern="^(field|exemplar|reference)$")
    distance: int | None = Field(default=None, ge=0, le=2)


class AddBody(BaseModel):
    version: int | None = Field(default=None, ge=1)
    url: str = Field(min_length=3, max_length=300)
    reason: str = Field(min_length=1, max_length=1000)
    panel: str = Field(default="field", pattern="^(field|exemplar|reference)$")
    resolves: str | None = Field(default=None, max_length=40)


class FinalizeBody(BaseModel):
    version: int | None = Field(default=None, ge=1)


class ExpandBody(BaseModel):
    version: int | None = Field(default=None, ge=1)
    approve_paid: bool = False


def invalid_response(e: BriefInvalid) -> JSONResponse:
    first = e.problems[0] if e.problems else BriefProblem("", "invalid brief")
    return JSONResponse(
        {
            "detail": f"invalid brief: {first}",
            "errors": [{"path": p.path, "message": p.message} for p in e.problems],
        },
        status_code=422,
    )


def parse_body(body: BriefBody) -> Brief:
    if (body.brief is None) == (body.yaml is None):
        raise BriefInvalid([BriefProblem("(request)", "send exactly one of `brief` or `yaml`")])
    data = parse_yaml(body.yaml) if body.yaml is not None else body.brief
    return validate_brief(data)


def stored_view(s: StoredBrief) -> dict[str, Any]:
    return {
        "brief": s.brief.model_dump(mode="json"),
        "yaml": s.yaml_text,
        "version": s.version,
        "content_hash": s.brief.content_hash(),
        "warnings": s.brief.warnings(),
    }


def make_router(
    *,
    store: BriefStore,
    data_dir: Path,
    llm_models: dict[str, str] | None = None,
    llm_batch: bool = True,
    month_cap_usd: float = 200.0,
    require_operator: Callable[..., str],
    same_origin: Callable[[Request], None],
    audit: Callable[[str, str, Request, int], None],
    read_conn: Callable[[], Iterator[psycopg.Connection[Any]]],
    write_conn: Callable[[], Iterator[psycopg.Connection[Any]]] | None = None,
    llm_client: Callable[[], Any] | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api", dependencies=[Depends(require_operator)])
    Conn = Annotated[psycopg.Connection[Any], Depends(read_conn)]

    def get_or_404(brief_id: str, version: int | None = None) -> StoredBrief:
        try:
            return store.get(check_id(brief_id), version)
        except BriefNotFound:
            raise HTTPException(404, "brief not found") from None
        except BriefInvalid as e:  # a stored file that no longer validates (edited by hand)
            raise HTTPException(500, f"stored brief does not validate: {e.problems[0]}") from None

    def runs(conn: psycopg.Connection[Any]) -> Any:
        from pigtail.briefs.cache import BriefRuns

        return BriefRuns(conn)

    @router.get("/briefs")
    def list_briefs(conn: Conn) -> dict[str, Any]:
        """D7 list: every brief with its current version, status, last run and budget."""
        last = runs(conn).summary_by_brief()
        items = []
        for s in store.list():
            b = store.get(s.brief_id).brief
            run = last.get(s.brief_id)
            items.append(
                {
                    "brief_id": s.brief_id,
                    "name": s.name,
                    "latest_version": s.latest_version,
                    "versions": s.versions,
                    "created_at": s.created_at,
                    "edited_at": s.edited_at,
                    "status": run["status"] if run else s.status,
                    "last_run": run,
                    "budget": b.budget.model_dump(mode="json"),
                }
            )
        return {"items": items, "exposure_warning": store.exposure_warning()}

    @router.post("/briefs", status_code=201, response_model=None)
    def create_brief(body: BriefBody, request: Request) -> dict[str, Any] | JSONResponse:
        """R18.1/R18.2: create a brief from the form or from YAML (same validation)."""
        same_origin(request)
        try:
            stored = store.create(parse_body(body))
        except BriefInvalid as e:
            return invalid_response(e)
        except BriefExists as e:
            raise HTTPException(409, str(e)) from None
        audit("brief_create", "/api/briefs", request, 201)
        return stored_view(stored)

    @router.post("/briefs/validate", response_model=None)
    def validate(body: BriefBody, request: Request) -> dict[str, Any] | JSONResponse:
        same_origin(request)
        try:
            b = parse_body(body)
        except BriefInvalid as e:
            return invalid_response(e)
        return {
            "ok": True,
            "brief": b.model_dump(mode="json"),
            "yaml": dump_yaml(b),
            "warnings": b.warnings(),
        }

    @router.get("/brief-template")
    def template() -> dict[str, Any]:
        text = example_path().read_text(encoding="utf-8")
        b = validate_brief(parse_yaml(text))
        return {"brief": b.model_dump(mode="json"), "yaml": text}

    @router.get("/briefs/{brief_id}")
    def get_brief(brief_id: str) -> dict[str, Any]:
        s = get_or_404(brief_id)
        out = stored_view(s)
        out["versions"] = [
            {
                "version": v.version,
                "edited_at": v.edited_at,
                "supersedes": v.supersedes,
                "content_hash": v.content_hash,
            }
            for v in store.versions(brief_id)
        ]
        return out

    @router.get("/briefs/{brief_id}/versions/{version}")
    def get_version(brief_id: str, version: int) -> dict[str, Any]:
        return stored_view(get_or_404(brief_id, version))

    @router.post("/briefs/{brief_id}/versions", response_model=None)
    def save_version(
        brief_id: str, body: BriefBody, request: Request
    ) -> dict[str, Any] | JSONResponse:
        """R18.4: an edit becomes a new version; unchanged content creates none."""
        same_origin(request)
        get_or_404(brief_id)
        try:
            b = parse_body(body)
        except BriefInvalid as e:
            return invalid_response(e)
        if b.brief_id != brief_id:
            return invalid_response(
                BriefInvalid(
                    [BriefProblem("brief_id", f"is {b.brief_id!r}; this brief is {brief_id!r}")]
                )
            )
        if body.base_version is not None and b.version not in (None, body.base_version):
            raise HTTPException(
                409,
                f"the brief says version {b.version} but base_version is "
                f"{body.base_version}; reload the latest version and re-apply your edit",
            )
        base = body.base_version if body.base_version is not None else b.version
        if base is None and not body.force_latest:
            raise HTTPException(
                409,
                "which version is this edit based on? Send base_version (or keep the brief's "
                "`version` field); to save over the latest version anyway, set force_latest",
            )
        try:
            stored, created = store.save_version(b, base_version=base)
        except VersionConflict as e:
            raise HTTPException(
                409,
                f"{e}; saving would revert the newer changes. Reload the latest version and "
                "re-apply your edit",
            ) from None
        if created:
            audit("brief_version", "/api/briefs/{id}/versions", request, 201)
        out = stored_view(stored)
        out["created"] = created
        return JSONResponse(jsonable(out), status_code=201 if created else 200)

    @router.post("/briefs/{brief_id}/expansion", response_model=None)
    def expansion(
        brief_id: str, body: ExpandBody, request: Request, conn: Conn
    ) -> dict[str, Any] | JSONResponse:
        """R18.7: an LLM expansion proposal for a stored brief version. Nothing is saved."""
        from pigtail.briefs.budget import BudgetStop
        from pigtail.briefs.expansion import make_guard, propose_expansion
        from pigtail.llm import LLMError, QueuePaused

        same_origin(request)
        s = get_or_404(brief_id, body.version)
        if llm_client is None:
            raise HTTPException(503, "no LLM client is configured on this instance")
        try:
            client = llm_client()
        except ValueError as e:
            raise HTTPException(503, f"LLM client not configured: {e}") from None
        from pigtail.llm.batch import PgCostLedger

        guard = make_guard(
            client,
            s.brief,
            approved_paid=body.approve_paid,
            month_cap_usd=month_cap_usd,
            spent_usd=PgCostLedger(conn).brief_total(brief_id),
            brief_ledger=lambda: PgCostLedger(conn).brief_total(brief_id),  # ADR-078.6
        )
        try:
            proposal = propose_expansion(s.brief, client, guard)
        except BudgetStop as e:
            audit("brief_expansion", "/api/briefs/{id}/expansion", request, 409)
            return JSONResponse(
                {"detail": f"nothing was sent to the model: {e}", "stop": e.to_dict()},
                status_code=409,
            )
        except QueuePaused as e:
            raise HTTPException(503, f"{e}; try again later") from None
        except LLMError as e:
            audit("brief_expansion", "/api/briefs/{id}/expansion", request, 502)
            raise HTTPException(
                502, f"expansion failed ({type(e).__name__}); nothing saved"
            ) from None
        audit("brief_expansion", "/api/briefs/{id}/expansion", request, 200)
        return proposal.to_dict()

    @router.get("/briefs/{brief_id}/diff")
    def diff(
        brief_id: str,
        v_from: Annotated[int, Query(alias="from", ge=1)],
        v_to: Annotated[int, Query(alias="to", ge=1)],
    ) -> dict[str, Any]:
        a, b = get_or_404(brief_id, v_from), get_or_404(brief_id, v_to)
        return diff_briefs(a.brief, b.brief).to_dict()

    @router.get("/briefs/{brief_id}/estimate")
    def estimate(
        brief_id: str, conn: Conn, version: Annotated[int | None, Query(ge=1)] = None
    ) -> dict[str, Any]:
        """R18.5, R15.11: the cost estimate shown before a run, per stage and model, against the
        brief's total cap and the monthly cap (always labelled an estimate)."""
        from pigtail.llm.batch import PgCostLedger

        s = get_or_404(brief_id, version)
        est, _plan = estimate_for(
            s.brief,
            store=store,
            data_dir=data_dir,
            models=llm_models,
            batch=llm_batch,
            month_cap_usd=month_cap_usd,
            last_run_version=runs(conn).last_run_version(brief_id),
            brief_spent_usd=PgCostLedger(conn).brief_total(brief_id),
            selection=selection_state(conn, s.brief) if s.brief.version is not None else None,
        )
        return est.to_dict()

    # --- shortlist review (R4.7, M22) ------------------------------------------------------
    WConn = Annotated[psycopg.Connection[Any], Depends(write_conn or read_conn)]

    def shortlist_of(conn: psycopg.Connection[Any], brief_id: str, version: int | None) -> Any:
        from pigtail.briefs.shortlist import Shortlist

        return Shortlist(conn, get_or_404(brief_id, version).brief)

    def sl_error(e: Exception) -> HTTPException:
        from pigtail.briefs.shortlist import NoShortlist, ShortlistFinal

        if isinstance(e, NoShortlist):
            return HTTPException(404, str(e))
        if isinstance(e, ShortlistFinal):
            return HTTPException(409, str(e))
        return HTTPException(422, str(e))

    @router.get("/briefs/{brief_id}/shortlist")
    def shortlist(
        brief_id: str,
        conn: Conn,
        version: Annotated[int | None, Query(ge=1)] = None,
        verdict: Annotated[
            str | None, Query(pattern="^(relevant|not_relevant|uncertain|not_judged)$")
        ] = None,
        panel: Annotated[str | None, Query(pattern="^(field|exemplar|reference)$")] = None,
        distance: Annotated[int | None, Query(ge=0, le=2)] = None,
    ) -> Any:
        """D7 shortlist review: every candidate with its sources, verdict, reason, rubric version
        and latest decision; precision against the 80 % target; reference cases to confirm."""
        sl = shortlist_of(conn, brief_id, version)
        return jsonable(sl.view(verdict=verdict, panel=panel, distance=distance))

    @router.post("/briefs/{brief_id}/shortlist/decisions")
    def shortlist_decide(brief_id: str, body: DecisionBody, request: Request, conn: WConn) -> Any:
        from pigtail.briefs.candidates import BadCandidate
        from pigtail.briefs.shortlist import ShortlistError

        same_origin(request)
        sl = shortlist_of(conn, brief_id, body.version)
        decision = "accept" if body.decision == "accept" else "reject"
        try:
            if body.candidates:
                n = sl.decide(body.candidates, decision, body.reason, reviewer="owner", via="ui")
            elif body.verdict or body.panel or body.distance is not None:
                n = sl.decide_where(
                    decision,
                    body.reason,
                    verdict=body.verdict,
                    panel=body.panel,
                    distance=body.distance,
                    reviewer="owner",
                    via="ui",
                )
            else:
                raise HTTPException(422, "give candidates or a filter")
        except (ShortlistError, BadCandidate) as e:
            audit("shortlist_decision", "/api/briefs/{id}/shortlist/decisions", request, 409)
            raise sl_error(e) from None
        audit("shortlist_decision", "/api/briefs/{id}/shortlist/decisions", request, 200)
        return {"decisions": n, "precision": sl.precision()}

    @router.post("/briefs/{brief_id}/shortlist/add")
    def shortlist_add(brief_id: str, body: AddBody, request: Request, conn: WConn) -> Any:
        from pigtail.briefs.candidates import BadCandidate
        from pigtail.briefs.shortlist import ShortlistError

        same_origin(request)
        sl = shortlist_of(conn, brief_id, body.version)
        panel = (
            "reference"
            if body.panel == "reference"
            else ("exemplar" if body.panel == "exemplar" else "field")
        )
        try:
            ref = sl.add(
                body.url,
                body.reason,
                panel=panel,
                resolves=body.resolves,
                reviewer="owner",
                via="ui",
            )
        except (ShortlistError, BadCandidate) as e:
            audit("shortlist_decision", "/api/briefs/{id}/shortlist/add", request, 409)
            raise sl_error(e) from None
        audit("shortlist_decision", "/api/briefs/{id}/shortlist/add", request, 200)
        return {"candidate_ref": ref}

    @router.post("/briefs/{brief_id}/shortlist/finalize")
    def shortlist_finalize(brief_id: str, body: FinalizeBody, request: Request, conn: WConn) -> Any:
        from pigtail.briefs.shortlist import ShortlistError

        same_origin(request)
        sl = shortlist_of(conn, brief_id, body.version)
        try:
            res = sl.finalize(reviewer="owner", via="ui")
        except ShortlistError as e:
            audit("shortlist_finalize", "/api/briefs/{id}/shortlist/finalize", request, 409)
            raise sl_error(e) from None
        audit("shortlist_finalize", "/api/briefs/{id}/shortlist/finalize", request, 200)
        return jsonable(res)

    return router


def jsonable(obj: Any) -> Any:
    from fastapi.encoders import jsonable_encoder

    return jsonable_encoder(obj)
