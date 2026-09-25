import { useState } from "react";
import { api } from "../api";
import { fmtTime, ROLE_LABEL, shortHash, STATE_LABEL } from "../format";
import { Pager } from "../pages/CasesPage";
import { Link } from "../router";
import { useAsync } from "../useAsync";
import { SnapshotLink } from "./SnapshotLink";

type SortKey = "fetched_at" | "source" | "reliability" | "deletion_state" | "retention_class";

const COLUMNS: { key: SortKey | null; label: string }[] = [
  { key: "fetched_at", label: "Captured (UTC)" },
  { key: "source", label: "Source" },
  { key: null, label: "Role" },
  { key: "reliability", label: "Reliability" },
  { key: "retention_class", label: "Retention" },
  { key: "deletion_state", label: "State" },
  { key: null, label: "Hash" },
  { key: null, label: "Snapshot" },
];

/** Evidence inventory (D1): sortable, filterable; each row opens its record and snapshot. */
export function EvidenceTable({ caseId }: { caseId: string }) {
  const [sort, setSort] = useState<SortKey>("fetched_at");
  const [order, setOrder] = useState<"asc" | "desc">("desc");
  const [source, setSource] = useState("");
  const [role, setRole] = useState("");
  const [offset, setOffset] = useState(0);
  const limit = 200;
  const q = { sort, order, source: source || undefined, role: role || undefined, limit, offset };
  const page = useAsync(() => api.caseEvidence(caseId, q), caseId + JSON.stringify(q));

  const toggle = (key: SortKey) => {
    if (key === sort) setOrder(order === "asc" ? "desc" : "asc");
    else {
      setSort(key);
      setOrder(key === "fetched_at" ? "desc" : "asc");
    }
    setOffset(0);
  };

  return (
    <section aria-label="Evidence inventory">
      <div className="filters">
        <label>
          Source
          <select
            value={source}
            onChange={(e) => {
              setSource(e.target.value);
              setOffset(0);
            }}
          >
            <option value="">all</option>
            {page.data?.sources.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>
        <label>
          Role
          <select
            value={role}
            onChange={(e) => {
              setRole(e.target.value);
              setOffset(0);
            }}
          >
            <option value="">all</option>
            {page.data?.roles.map((r) => (
              <option key={r} value={r}>
                {ROLE_LABEL[r] ?? r}
              </option>
            ))}
          </select>
        </label>
      </div>
      {page.status === "error" && <p className="error">{page.error.message}</p>}
      {page.data && (
        <>
          <table className="data">
            <thead>
              <tr>
                {COLUMNS.map((c) => (
                  <th
                    key={c.label}
                    aria-sort={c.key === sort ? (order === "asc" ? "ascending" : "descending") : undefined}
                  >
                    {c.key ? (
                      <button type="button" className="sort" onClick={() => c.key && toggle(c.key)}>
                        {c.label}
                        {c.key === sort ? (order === "asc" ? " ▲" : " ▼") : ""}
                      </button>
                    ) : (
                      c.label
                    )}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {page.data.items.map((ev) => (
                <tr key={ev.id}>
                  <td>
                    <Link href={`/evidence/${ev.id}`}>{fmtTime(ev.fetched_at)}</Link>
                  </td>
                  <td>{ev.source}</td>
                  <td>{(ev.roles ?? []).map((r) => ROLE_LABEL[r] ?? r).join(", ")}</td>
                  <td>{ev.reliability}</td>
                  <td>{ev.retention_class}</td>
                  <td>{STATE_LABEL[ev.deletion_state] ?? ev.deletion_state}</td>
                  <td>
                    <code title={ev.content_hash}>{shortHash(ev.content_hash)}</code>
                  </td>
                  <td>
                    <SnapshotLink ev={ev} label="Open" />
                  </td>
                </tr>
              ))}
              {page.data.items.length === 0 && (
                <tr>
                  <td colSpan={COLUMNS.length} className="muted">
                    No evidence.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
          <Pager total={page.data.total} limit={limit} offset={offset} onOffset={setOffset} />
        </>
      )}
    </section>
  );
}
