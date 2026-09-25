import { useState } from "react";
import { api, type CaseDetail } from "../api";
import { EvidenceTable } from "../components/EvidenceTable";
import { TimelineView } from "../components/Timeline";
import { fmtNum, fmtTime } from "../format";
import { Link, navigate, useLocation } from "../router";
import { useAsync } from "../useAsync";

type Tab = "timeline" | "evidence";

function DetectionPanel({ d }: { d: CaseDetail }) {
  const [open, setOpen] = useState(false);
  const det = d.detection;
  if (!det) return <p className="muted">No automatic detection for this case (trigger: {d.case.trigger}).</p>;
  const hours = d.detection_hours.filter((h) => h.stars_filtered > 0 || h.scan_status !== "ok");
  const cov = det.coverage;
  return (
    <section className="card detection" aria-label="Detection metrics">
      <dl className="metrics">
        <div>
          <dt>Stars, 48 h (filtered)</dt>
          <dd>{fmtNum(det.stars_48h)}</dd>
        </div>
        <div>
          <dt>Stars, 48 h (raw)</dt>
          <dd>{fmtNum(det.stars_48h_raw)}</dd>
        </div>
        <div>
          <dt>Forks, 48 h</dt>
          <dd>{fmtNum(det.forks_48h)}</dd>
        </div>
        <div>
          <dt>z vs 30-day baseline</dt>
          <dd>{fmtNum(det.z_score, 1)}</dd>
        </div>
        <div>
          <dt>Baseline quality</dt>
          <dd>{det.baseline_quality}</dd>
        </div>
        <div>
          <dt>Coverage ratio</dt>
          <dd>{cov.ratio === null ? "unknown" : fmtNum(cov.ratio, 3)}</dd>
        </div>
      </dl>
      <p className="muted small">
        Detected at {fmtTime(det.detected_hour)} by {det.rule_version} (threshold ≥ {det.threshold_min_stars} stars and
        z ≥ {det.threshold_sigma}); bot filter {det.bot_filter_version}. Coverage source {cov.source}, window{" "}
        {fmtTime(cov.window_start)} – {fmtTime(cov.window_end)}, {fmtNum(cov.observed_stars)} stars observed
        {cov.reference_stars === null
          ? "; no independent reference count yet (unknown)."
          : `; reference ${fmtNum(cov.reference_stars)} from ${cov.reference_source ?? "unknown"}.`}
      </p>
      <button type="button" className="link-button" aria-expanded={open} onClick={() => setOpen(!open)}>
        {open ? "Hide" : "Show"} the 48 hourly buckets behind these numbers
      </button>
      {open && (
        <table className="data compact">
          <thead>
            <tr>
              <th>Hour</th>
              <th>Scan</th>
              <th className="num">Stars raw</th>
              <th className="num">Stars filtered</th>
              <th className="num">Forks</th>
              <th>Evidence</th>
            </tr>
          </thead>
          <tbody>
            {hours.map((h) => (
              <tr key={h.hour}>
                <td>{fmtTime(h.hour)}</td>
                <td>{h.scan_status ?? "not scanned (unknown)"}</td>
                <td className="num">{h.scan_status ? fmtNum(h.stars_raw) : "unknown"}</td>
                <td className="num">
                  {h.scan_status ? fmtNum(h.stars_filtered) : "unknown"}
                  {h.lockstep ? " ⚑" : ""}
                </td>
                <td className="num">{h.scan_status ? fmtNum(h.forks_filtered) : "unknown"}</td>
                <td>{h.evidence_id ? <Link href={`/evidence/${h.evidence_id}`}>{h.evidence_id}</Link> : "—"}</td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr>
              <td colSpan={3}>Sum over 48 h (hours with no stars omitted above)</td>
              <td className="num">{fmtNum(d.detection_hours.reduce((a, h) => a + h.stars_filtered, 0))}</td>
              <td colSpan={2} />
            </tr>
          </tfoot>
        </table>
      )}
    </section>
  );
}

export function CasePage({ id }: { id: string }) {
  const { search } = useLocation();
  const tab: Tab = search.get("tab") === "evidence" ? "evidence" : "timeline";
  const detail = useAsync(() => api.case(id), id);

  if (detail.status === "error") return <p className="error">{detail.error.message}</p>;
  if (detail.status !== "ok") return <p className="muted">Loading…</p>;
  const d = detail.data;
  const setTab = (t: Tab) => navigate(`/cases/${id}${t === "evidence" ? "?tab=evidence" : ""}`);

  return (
    <>
      <nav className="crumbs">
        <Link href="/cases">Cases</Link> / {d.repo.full_name}
      </nav>
      <h1>
        {d.repo.full_name} <span className={`pill pill-${d.case.status}`}>{d.case.status.replace("_", "-")}</span>
      </h1>
      <p className="muted">
        Case {d.case.id} · trigger {d.case.trigger} · opened {fmtTime(d.case.opened_at)}
        {d.case.closed_at ? ` · closed ${fmtTime(d.case.closed_at)}` : ""} · repo {d.repo.id}
      </p>
      <aside className="caveat" role="note">
        <strong>Coverage caveat.</strong> {d.caveats[0]}
      </aside>
      <DetectionPanel d={d} />
      <div className="tabs" role="tablist">
        <button type="button" role="tab" aria-selected={tab === "timeline"} onClick={() => setTab("timeline")}>
          Timeline
        </button>
        <button type="button" role="tab" aria-selected={tab === "evidence"} onClick={() => setTab("evidence")}>
          Evidence ({fmtNum(Object.values(d.evidence_counts).reduce((a, b) => a + b, 0))} links)
        </button>
        <span className="tab-note">uncoded preview</span>
      </div>
      <div role="tabpanel">
        {tab === "timeline" ? (
          <TimelineView caseId={id} openedAt={d.case.opened_at} />
        ) : (
          <EvidenceTable caseId={id} />
        )}
      </div>
    </>
  );
}
