// D7 `/briefs/:id/shortlist` (M22; PRD R4.7): the shortlist review of the brief's latest version.
// Writes are same-origin JSON POSTs; every decision is logged with a reason, the reviewer role
// and the time. Runs themselves start from the CLI (`pigtail run --brief <id>`).
import { useState } from "react";
import { api, type ShortlistDecisionBody } from "../api";
import { type AddRequest, ShortlistReview } from "../components/ShortlistReview";
import { Link } from "../router";
import { useAsync } from "../useAsync";

export function ShortlistPage({ id }: { id: string }) {
  const [tick, setTick] = useState(0);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const view = useAsync(() => api.shortlist(id), `shortlist:${id}:${tick}`);

  const act = (label: string, call: () => Promise<unknown>) => {
    setBusy(true);
    call().then(
      () => {
        setMessage({ ok: true, text: label });
        setTick((t) => t + 1);
      },
      (e: unknown) => setMessage({ ok: false, text: e instanceof Error ? e.message : String(e) }),
    ).finally(() => setBusy(false));
  };

  return (
    <section>
      <p className="crumbs">
        <Link href="/briefs">Briefs</Link> / <Link href={`/briefs/${id}`}>{id}</Link> / shortlist
      </p>
      <h1>
        Shortlist review {view.data && <span className="muted">v{view.data.brief_version}</span>}
      </h1>
      {message && (
        <p role={message.ok ? "status" : "alert"} className={message.ok ? undefined : "error"}>
          {message.text}
        </p>
      )}
      {view.status === "error" && (
        <p className="error" role="alert">
          {view.error.message} (run the brief first: <code>pigtail run --brief {id}</code>)
        </p>
      )}
      {!view.data && view.status === "loading" && <p className="muted">Loading…</p>}
      {view.data && (
        <ShortlistReview
          view={view.data}
          busy={busy}
          onDecide={(body: ShortlistDecisionBody) => {
            const version = view.data?.brief_version ?? 1;
            act(`${body.decision} logged`, () => api.shortlistDecide(id, { ...body, version }));
          }}
          onAdd={(body: AddRequest) => {
            const version = view.data?.brief_version ?? 1;
            act("added", () => api.shortlistAdd(id, { ...body, version }));
          }}
          onFinalize={() => {
            const version = view.data?.brief_version ?? 1;
            act("shortlist finalized", () => api.shortlistFinalize(id, { version }));
          }}
        />
      )}
    </section>
  );
}
