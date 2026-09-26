import { useState } from "react";
import { api, type CaseStatus, type CaseSummary } from "../api";
import { LaunchModeStrip } from "../components/LaunchModeStrip";
import { fmtNum, fmtTime, fromInputValue } from "../format";
import { Link } from "../router";
import { useAsync } from "../useAsync";

type Sort = "recency" | "velocity";

function CaseRow({ c }: { c: CaseSummary }) {
  return (
    <tr>
      <td>
        <Link href={`/cases/${c.id}`}>{c.repo_full_name}</Link>
      </td>
      <td>
        <span className={`pill pill-${c.status}`}>{c.status.replace("_", "-")}</span>
      </td>
      <td>{c.trigger}</td>
      <td>{fmtTime(c.opened_at)}</td>
      <td className="num">{fmtNum(c.stars_48h)}</td>
      <td className="num">{c.z_score === null ? "—" : fmtNum(c.z_score, 1)}</td>
      <td>{c.baseline_quality ?? "—"}</td>
      <td className="num">{c.coverage_ratio === null ? "unknown" : fmtNum(c.coverage_ratio, 3)}</td>
      <td className="num">{fmtNum(c.evidence_linked)}</td>
    </tr>
  );
}

export function CasesPage() {
  const [status, setStatus] = useState<CaseStatus | "">("");
  const [sort, setSort] = useState<Sort>("recency");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [offset, setOffset] = useState(0);
  const limit = 50;
  const q = {
    status: status || undefined,
    sort,
    from: fromInputValue(from),
    to: fromInputValue(to),
    limit,
    offset,
  };
  const cases = useAsync(() => api.cases(q), JSON.stringify(q));

  return (
    <>
      <LaunchModeStrip />
      <section>
        <h1>Cases</h1>
        <div className="filters" role="group" aria-label="Filters">
          <label>
            Status
            <select
              value={status}
              onChange={(e) => {
                setStatus(e.target.value as CaseStatus | "");
                setOffset(0);
              }}
            >
              <option value="">all</option>
              <option value="live">live</option>
              <option value="pre_launch">pre-launch</option>
              <option value="closed">closed</option>
            </select>
          </label>
          <label>
            Opened from (UTC)
            <input type="datetime-local" value={from} onChange={(e) => {
                setFrom(e.target.value);
                setOffset(0);
              }} />
          </label>
          <label>
            to
            <input type="datetime-local" value={to} onChange={(e) => {
                setTo(e.target.value);
                setOffset(0);
              }} />
          </label>
          <label>
            Sort
            <select value={sort} onChange={(e) => {
                setSort(e.target.value as Sort);
                setOffset(0);
              }}>
              <option value="recency">recency</option>
              <option value="velocity">velocity (48 h stars)</option>
            </select>
          </label>
        </div>
        {cases.status === "error" && <p className="error">{cases.error.message}</p>}
        {cases.data && (
          <>
            <table className="data">
              <thead>
                <tr>
                  <th>Repo</th>
                  <th>Status</th>
                  <th>Trigger</th>
                  <th>Opened</th>
                  <th className="num" title="48 h stars as recorded when the case was opened (by the global detection removed in M11)">
                    Stars 48 h
                  </th>
                  <th className="num">z</th>
                  <th>Baseline</th>
                  <th className="num" title="Observed / reference stars, when a reference was checked">
                    Coverage
                  </th>
                  <th className="num">Evidence</th>
                </tr>
              </thead>
              <tbody>
                {cases.data.items.map((c) => (
                  <CaseRow key={c.id} c={c} />
                ))}
                {cases.data.items.length === 0 && (
                  <tr>
                    <td colSpan={9} className="muted">
                      No cases match.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
            <Pager total={cases.data.total} limit={limit} offset={offset} onOffset={setOffset} />
          </>
        )}
      </section>
    </>
  );
}

export function Pager({
  total,
  limit,
  offset,
  onOffset,
}: {
  total: number;
  limit: number;
  offset: number;
  onOffset: (o: number) => void;
}) {
  if (total <= limit) return <p className="muted small">{fmtNum(total)} total</p>;
  return (
    <div className="pager">
      <button type="button" disabled={offset === 0} onClick={() => onOffset(Math.max(0, offset - limit))}>
        Previous
      </button>
      <span className="muted">
        {fmtNum(offset + 1)}–{fmtNum(Math.min(total, offset + limit))} of {fmtNum(total)}
      </span>
      <button type="button" disabled={offset + limit >= total} onClick={() => onOffset(offset + limit)}>
        Next
      </button>
    </div>
  );
}
