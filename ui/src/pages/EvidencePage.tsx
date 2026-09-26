import type { ReactNode } from "react";
import { api } from "../api";
import { SnapshotLink } from "../components/SnapshotLink";
import { fmtTime, STATE_LABEL } from "../format";
import { Link } from "../router";
import { useAsync } from "../useAsync";

/** One evidence record: what it is, where its snapshot is, and what points at it (R13.2). */
export function EvidencePage({ id }: { id: string }) {
  const res = useAsync(() => api.evidence(id), id);
  if (res.status === "error") return <p className="error">{res.error.message}</p>;
  if (res.status !== "ok") return <p className="muted">Loading…</p>;
  const { evidence: ev, links, retention } = res.data;
  const rows: [string, ReactNode][] = [
    ["Source", ev.source],
    ["URL", ev.url ?? "—"],
    ["Captured", fmtTime(ev.fetched_at)],
    ["Content hash (SHA-256)", <code key="h">{ev.content_hash}</code>],
    ["Content type", ev.content_type ?? "unknown"],
    ["HTTP status", ev.http_status ?? "unknown"],
    ["Reliability", ev.reliability],
    ["Terms basis", ev.terms_basis],
    ["Collector", ev.collector_version],
    ["Retention class", retention.retention_class],
    ["Retention rule", retention.rule],
    ["Raw bytes due to be dropped", retention.raw_drop_due_at ? fmtTime(retention.raw_drop_due_at) : "not scheduled"],
    ["State", STATE_LABEL[ev.deletion_state] ?? ev.deletion_state],
    ["Run", ev.run_id ?? "—"],
  ];
  return (
    <>
      <nav className="crumbs">
        {links.case_id ? <Link href={`/cases/${links.case_id}?tab=evidence`}>Case evidence</Link> : <Link href="/cases">Cases</Link>}{" "}
        / {ev.id}
      </nav>
      <h1>Evidence {ev.id}</h1>
      <p>
        <SnapshotLink ev={ev} />
        {ev.snapshot.in_store === false && <span className="error"> — bytes missing from the snapshot store</span>}
      </p>
      <p className="muted small">
        Snapshots open in a new tab, sandboxed (no scripts, no external requests). The server re-checks the SHA-256
        before sending, and each view is written to the audit log.
      </p>
      <dl className="record">
        {rows.map(([k, v]) => (
          <div key={k}>
            <dt>{k}</dt>
            <dd>{v}</dd>
          </div>
        ))}
      </dl>
      <h2>Referenced by</h2>
      <ul className="refs">
        {links.case_id && (
          <li>
            Case <Link href={`/cases/${links.case_id}`}>{links.case_id}</Link>
          </li>
        )}
        {links.repo_id && <li>Repo {links.repo_id}</li>}
        {links.star_history_days.map((d) => (
          <li key={`${d.repo_host_id}-${d.day}`}>
            Star history, repo {d.repo_host_id}, day {d.day}
          </li>
        ))}
        {links.hn_rank_polls.map((t) => (
          <li key={t}>HN front-page rank poll at {fmtTime(t)}</li>
        ))}
        {links.hn_stories.map((s) => (
          <li key={s.item_id}>HN story {s.item_id}</li>
        ))}
        {links.hn_mentions.map((m) => (
          <li key={m.item_id}>
            HN mention {m.item_id}
            {m.case_id ? (
              <>
                {" "}
                in case <Link href={`/cases/${m.case_id}`}>{m.case_id}</Link>
              </>
            ) : null}
          </li>
        ))}
        {links.same_bytes_evidence_ids.map((e) => (
          <li key={e}>
            Same bytes captured as <Link href={`/evidence/${e}`}>{e}</Link>
          </li>
        ))}
      </ul>
    </>
  );
}
