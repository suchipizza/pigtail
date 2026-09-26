"""FastAPI app: read-only D1 API (R14.2), D7 briefs (M12), operator login (R13.3, CB-19) and
the built UI.

Routes (all `/api/*` except login require an operator session):
    POST /api/auth/login, POST /api/auth/logout, GET /api/auth/me
    GET  /api/cases                       filters: status, from, to; sort: recency|velocity
    GET  /api/launch-mode                 launch-mode windows active now (D1 strip; read-only)
    GET  /api/cases/{id}                  case, repo, detection metrics + the 48 h behind them
    GET  /api/cases/{id}/timeline         GitHub + HN lanes and evidence events (from/to/bucket)
    GET  /api/cases/{id}/evidence         evidence inventory (sortable, filterable, paged)
    GET  /api/evidence/{id}               one evidence record, its links and retention state
    GET  /api/snapshots/{hash}            the raw snapshot, hash re-verified; 410 once dropped
    /api/briefs...                        D7 research briefs, read and write (api/briefs.py)
Anything else is served from the built UI (`ui/dist`, single-page app fallback).
"""

# No `from __future__ import annotations`: FastAPI must resolve the closure-local dependency
# annotations (`Conn`, `require_operator`) at definition time.
import re
from collections.abc import Callable, Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, Literal

import psycopg
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from psycopg_pool import ConnectionPool
from pydantic import BaseModel, Field

from pigtail import __version__
from pigtail.api import queries as q
from pigtail.api.auth import (
    COOKIE_NAME,
    AuthStore,
    client_key,
    is_loopback_host,
    verify_password,
)
from pigtail.api.briefs import make_router as make_briefs_router
from pigtail.api.settings import UISettings
from pigtail.briefs.store import BriefStore
from pigtail.capture.snapshots import (
    SnapshotIntegrityError,
    SnapshotNotFound,
    SnapshotStore,
)
from pigtail.config import Settings
from pigtail.llm import LLMClient
from pigtail.llm import build_client as build_llm_client

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[a-z]+_[0-9a-f]{16,40}$")
# The app never loads third-party content; snapshots get a stricter, sandboxed policy.
APP_CSP = (
    "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
    "connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; "
    "form-action 'self'"
)
SNAPSHOT_CSP = "sandbox; default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'"
GONE_MESSAGES = {
    "raw_dropped": (
        "The raw bytes of this snapshot were dropped under the retention policy (DPIA CB-01/"
        "CB-04). The evidence record keeps its hash, URL and fetch time; replay can re-download "
        "and re-verify the hash where the source still serves it."
    ),
    "deleted_upstream": (
        "The item was deleted upstream, so this snapshot was dropped (deletion sync, DPIA CB-02)."
        " The evidence record keeps its hash, URL and fetch time."
    ),
}


class LoginBody(BaseModel):
    password: str = Field(min_length=1, max_length=1024)


def _open_pools(conninfo: str) -> tuple[ConnectionPool[Any], ConnectionPool[Any]]:
    # Reads: every transaction is READ ONLY at the server (a bug cannot write), in UTC.
    read = ConnectionPool(
        conninfo,
        min_size=1,
        max_size=8,
        kwargs={"options": "-c default_transaction_read_only=on -c timezone=UTC"},
        open=True,
        name="pigtail-ui-read",
    )
    # Writes: sessions and audit rows only.
    write = ConnectionPool(
        conninfo,
        min_size=1,
        max_size=4,
        kwargs={"options": "-c timezone=UTC"},
        open=True,
        name="pigtail-ui-write",
    )
    return read, write


def create_app(
    *,
    conninfo: str,
    ui: UISettings,
    store: SnapshotStore,
    settings: Settings | None = None,
    llm_client: Callable[[], LLMClient] | None = None,
) -> FastAPI:
    capture = settings or Settings.from_env({})
    pools: dict[str, ConnectionPool[Any]] = {}

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> Any:
        read, write = _open_pools(conninfo)
        pools["read"], pools["write"] = read, write
        try:
            yield
        finally:
            read.close()
            write.close()

    app = FastAPI(
        title="pigtail",
        version=__version__,
        lifespan=lifespan,
        docs_url=None,  # no public API explorer or schema: the app is private by default
        redoc_url=None,
        openapi_url=None,
    )
    app.state.pools = pools  # tests and health checks inspect the pools

    def auth_store() -> AuthStore:
        return AuthStore(pools["write"], ui)

    def read_conn() -> Iterator[psycopg.Connection[Any]]:
        with pools["read"].connection() as conn:
            yield conn

    def client_of(request: Request) -> str | None:
        return client_key(request.client.host if request.client else None, ui.password_hash)

    def require_operator(request: Request) -> str:
        th = auth_store().check_session(request.cookies.get(COOKIE_NAME))
        if th is None:
            raise HTTPException(401, "login required")
        request.state.session = th
        return th

    def same_origin(request: Request) -> None:
        """Every POST (login, logout, brief writes): JSON only and same origin (no cross-site
        form or fetch)."""
        ctype = request.headers.get("content-type", "").split(";")[0].strip().lower()
        if ctype != "application/json":
            raise HTTPException(415, "application/json required")
        site = request.headers.get("sec-fetch-site")
        if site is not None and site not in ("same-origin", "none"):
            raise HTTPException(403, "cross-site request refused")
        origin = request.headers.get("origin")
        if origin is not None:
            host = request.headers.get("host", "")
            if origin.split("://", 1)[-1].rstrip("/").lower() != host.lower():
                raise HTTPException(403, "cross-origin request refused")

    def secure_cookie(request: Request) -> bool:
        if ui.secure_cookie is not None:
            return ui.secure_cookie
        return not is_loopback_host(request.headers.get("host") or request.url.hostname)

    @app.middleware("http")
    async def security_headers(request: Request, call_next: Any) -> Response:
        resp: Response = await call_next(request)
        h = resp.headers
        h.setdefault("X-Content-Type-Options", "nosniff")
        h.setdefault("Referrer-Policy", "no-referrer")
        h.setdefault("X-Frame-Options", "DENY")
        h.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        h.setdefault("Content-Security-Policy", APP_CSP)
        if request.url.path.startswith("/api/"):
            h.setdefault("Cache-Control", "no-store")
        return resp

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict[str, bool]:
        return {"ok": True}

    # --- auth ------------------------------------------------------------------------------------
    public = APIRouter(prefix="/api/auth")

    @public.post("/login")
    def login(body: LoginBody, request: Request, response: Response) -> dict[str, Any]:
        """R13.3 / CB-19: operator login; rate-limited; every attempt is audited."""
        same_origin(request)
        a = auth_store()
        client = client_of(request)
        decision = a.login_allowed(client)
        if not decision.allowed:
            a.audit("login_rate_limited", "/api/auth/login", status=429, client=client)
            raise HTTPException(
                429,
                "too many failed logins; try again later",
                headers={"Retry-After": str(decision.retry_after_s)},
            )
        if not verify_password(ui.password_hash, body.password):
            a.audit("login_failure", "/api/auth/login", status=401, client=client)
            raise HTTPException(401, "wrong password")
        token, th = a.create_session()
        a.audit("login_success", "/api/auth/login", status=200, session=th, client=client)
        response.set_cookie(
            COOKIE_NAME,
            token,
            max_age=int(ui.session_hours * 3600),
            path="/",
            httponly=True,
            samesite="strict",
            secure=secure_cookie(request),
        )
        return {"authenticated": True}

    @public.post("/logout")
    def logout(request: Request, response: Response) -> dict[str, Any]:
        same_origin(request)
        a = auth_store()
        token = request.cookies.get(COOKIE_NAME)
        th = a.check_session(token)
        a.end_session(token)
        if th is not None:
            a.audit("logout", "/api/auth/logout", status=200, session=th, client=client_of(request))
        response.delete_cookie(COOKIE_NAME, path="/", samesite="strict", httponly=True)
        return {"authenticated": False}

    @public.get("/me")
    def me(_op: Annotated[str, Depends(require_operator)]) -> dict[str, Any]:
        return {"authenticated": True, "version": __version__}

    app.include_router(public)

    # --- read-only API (R14.2) ---------------------------------------------------------------
    api = APIRouter(prefix="/api", dependencies=[Depends(require_operator)])
    Conn = Annotated[psycopg.Connection[Any], Depends(read_conn)]

    def check_id(value: str) -> str:
        if not _ID_RE.match(value):
            raise HTTPException(404, "not found")
        return value

    @api.get("/cases")
    def cases(
        conn: Conn,
        status: Literal["live", "pre_launch", "closed"] | None = None,
        date_from: Annotated[datetime | None, Query(alias="from")] = None,
        date_to: Annotated[datetime | None, Query(alias="to")] = None,
        sort: q.CaseSort = "recency",
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> dict[str, Any]:
        out = q.list_cases(
            conn,
            status=status,
            date_from=date_from,
            date_to=date_to,
            sort=sort,
            limit=limit,
            offset=offset,
        )
        out["caveats"] = [q.COVERAGE_CAVEAT, q.UNCODED_NOTE]
        return out

    @api.get("/launch-mode")
    def launch_mode(conn: Conn) -> dict[str, Any]:
        """D1 "Launch mode" strip (ADR-048.2, ADR-049.1): tracked projects and briefs whose
        launch-mode window is active now. Read-only; windows are declared or detected in M14."""
        return q.launch_mode(conn, datetime.now(UTC))

    @api.get("/cases/{case_id}")
    def case(case_id: str, conn: Conn) -> dict[str, Any]:
        out = q.get_case(conn, check_id(case_id))
        if out is None:
            raise HTTPException(404, "case not found")
        return out

    @api.get("/cases/{case_id}/timeline")
    def case_timeline(
        case_id: str,
        conn: Conn,
        date_from: Annotated[datetime | None, Query(alias="from")] = None,
        date_to: Annotated[datetime | None, Query(alias="to")] = None,
        bucket: q.Bucket | None = None,
    ) -> dict[str, Any]:
        try:
            out = q.timeline(conn, check_id(case_id), start=date_from, end=date_to, bucket=bucket)
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        if out is None:
            raise HTTPException(404, "case not found")
        return out

    @api.get("/cases/{case_id}/evidence")
    def case_evidence(
        case_id: str,
        conn: Conn,
        sort: q.EvidenceSort = "fetched_at",
        order: Literal["asc", "desc"] = "desc",
        source: Annotated[str | None, Query(pattern=r"^[a-z0-9_]+$")] = None,
        role: Annotated[str | None, Query(pattern=r"^[a-z_]+$")] = None,
        limit: Annotated[int, Query(ge=1, le=1000)] = 200,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> dict[str, Any]:
        if not q.case_exists(conn, check_id(case_id)):
            raise HTTPException(404, "case not found")
        return q.case_evidence(
            conn,
            case_id,
            sort=sort,
            order=order,
            source=source,
            role=role,
            limit=limit,
            offset=offset,
        )

    @api.get("/evidence/{evidence_id}")
    def evidence(evidence_id: str, conn: Conn) -> dict[str, Any]:
        out = q.get_evidence(conn, check_id(evidence_id))
        if out is None:
            raise HTTPException(404, "evidence not found")
        ev = out["evidence"]
        out["retention"] = retention_info(ev, capture, conn)
        if ev["snapshot"]["available"]:
            try:
                ev["snapshot"]["in_store"] = store.exists(ev["content_hash"])
            except Exception:  # the store being down must not hide the record
                ev["snapshot"]["in_store"] = None
        return out

    @api.get("/snapshots/{content_hash}")
    def snapshot(
        content_hash: str,
        request: Request,
        conn: Conn,
        evidence: Annotated[str | None, Query(pattern=r"^ev_[0-9a-f]{24}$")] = None,
    ) -> Response:
        """R13.2: open the snapshot behind an evidence record. Hash re-verified; audited (CB-19)."""
        a = auth_store()
        session = getattr(request.state, "session", None)
        route = "/api/snapshots/{hash}"
        client = client_of(request)
        if not _HASH_RE.match(content_hash):
            raise HTTPException(404, "not a snapshot hash")
        rows = q.snapshot_states(conn, content_hash)
        if evidence is not None:
            rows = [r for r in rows if r["id"] == evidence]
        if not rows:
            # Only snapshots referenced by an evidence record are served.
            raise HTTPException(404, "no evidence record references this snapshot")
        ev_id = evidence or rows[0]["id"]

        def audit(event: str, status: int) -> None:
            a.audit(
                event,
                route,
                status=status,
                evidence_id=ev_id,
                content_hash=content_hash,
                session=session,
                client=client,
            )

        gone = [r["deletion_state"] for r in rows if r["deletion_state"] != "present"]
        if not any(r["deletion_state"] == "present" for r in rows):
            audit("snapshot_gone", 410)
            return gone_response(gone[0], content_hash, ev_id)
        try:
            data = store.get(content_hash)
        except SnapshotNotFound:
            if gone:
                audit("snapshot_gone", 410)
                return gone_response(gone[0], content_hash, ev_id)
            audit("snapshot_missing", 404)
            raise HTTPException(404, "snapshot bytes missing from the snapshot store") from None
        except SnapshotIntegrityError:
            audit("snapshot_integrity_failure", 500)
            raise HTTPException(
                500, "snapshot failed hash verification; it was not served"
            ) from None
        audit("snapshot_view", 200)
        ctype = next((r["content_type"] for r in rows if r["content_type"]), None)
        ctype = ctype or "application/octet-stream"
        binary = not (ctype.startswith("text/") or "json" in ctype or "xml" in ctype)
        disposition = "attachment" if binary else "inline"
        return Response(
            content=data,
            media_type=ctype,
            headers={
                "Content-Security-Policy": SNAPSHOT_CSP,
                "Content-Disposition": f'{disposition}; filename="{content_hash}"',
                "X-Content-SHA256": content_hash,
                "Cache-Control": "private, no-store",
            },
        )

    # --- D7 briefs (M12): read and write, behind the same login -----------------------------
    llm_clients: dict[str, LLMClient] = {}

    def shared_llm_client() -> LLMClient:
        """One `LLMClient` per app, built on first use (R18.7 expansion proposals)."""
        if "default" not in llm_clients:
            llm_clients["default"] = build_llm_client(capture)
        return llm_clients["default"]

    def audit_write(event: str, route: str, request: Request, status: int) -> None:
        auth_store().audit(
            event,
            route,
            status=status,
            session=getattr(request.state, "session", None),
            client=client_of(request),
        )

    app.include_router(
        make_briefs_router(
            store=BriefStore.from_settings(capture),
            data_dir=capture.data_dir,
            llm_models={str(k): v for k, v in capture.llm_models.items()},
            llm_batch=capture.llm_batch,
            month_cap_usd=capture.budget_usd_month,
            require_operator=require_operator,
            same_origin=same_origin,
            audit=audit_write,
            read_conn=read_conn,
            llm_client=llm_client or shared_llm_client,
        )
    )

    @api.get("/{rest:path}", include_in_schema=False)
    def api_not_found(rest: str) -> None:
        raise HTTPException(404, "no such API route")

    app.include_router(api)
    mount_ui(app, ui.dist_dir)
    return app


def gone_response(state: str, content_hash: str, evidence_id: str) -> JSONResponse:
    return JSONResponse(
        {
            "detail": GONE_MESSAGES.get(state, "The snapshot is no longer available."),
            "deletion_state": state,
            "content_hash": content_hash,
            "evidence_id": evidence_id,
        },
        status_code=410,
    )


def retention_info(ev: dict[str, Any], s: Settings, conn: Any = None) -> dict[str, Any]:
    """When the raw bytes are due to be dropped (retention policy), or null if kept.

    Person-level snapshots follow R19.9 (report final + 12 months, else the
    PERSON_LEVEL_RETENTION_DAYS ceiling; `pigtail.privacy.snapshot_retention`) when a database
    connection is given; GH Archive dumps keep their shorter rule."""
    fetched: datetime = ev["fetched_at"]
    rules: list[tuple[datetime, str]] = []
    if ev["retention_class"] == "person_level_24m" and ev["deletion_state"] == "present":
        at: datetime | None = fetched + timedelta(days=s.person_level_retention_days)
        rule = "person-level: PERSON_LEVEL_RETENTION_DAYS ceiling"
        if conn is not None:
            from pigtail.privacy.snapshot_retention import evidence_due

            at, rule = evidence_due(
                conn,
                content_hash=ev["content_hash"],
                retention_class=ev["retention_class"],
                after_report_days=s.snapshot_after_report_days,
                ceiling_days=s.person_level_retention_days,
            )
        if at is not None:
            rules.append((at, rule))
    if ev["source"] == "gharchive":
        rules.append(
            (
                fetched + timedelta(days=s.gharchive_raw_retention_days),
                "GH Archive: GHARCHIVE_RAW_RETENTION_DAYS",
            )
        )
    due, rule = min(rules) if rules else (None, "kept (no raw-retention limit for this class)")
    return {
        "retention_class": ev["retention_class"],
        "deletion_state": ev["deletion_state"],
        "rule": rule,
        "raw_drop_due_at": due,
    }


def mount_ui(app: FastAPI, dist: Path) -> None:
    """Serve the built UI; unknown non-API paths fall back to index.html (client routing)."""
    index = dist / "index.html"
    assets = dist / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> Response:
        if path.startswith("api/"):
            raise HTTPException(404, "not found")
        candidate = (dist / path).resolve()
        if path and candidate.is_file() and dist.resolve() in candidate.parents:
            return FileResponse(candidate)
        if index.is_file():
            return FileResponse(index, headers={"Cache-Control": "no-cache"})
        return JSONResponse(
            {"detail": "UI not built: run `pnpm --dir ui install && pnpm --dir ui build`"},
            status_code=503,
        )
