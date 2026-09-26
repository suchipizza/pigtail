import { api, type LaunchModeWindow } from "../api";
import { fmtTime } from "../format";
import { Link } from "../router";
import { useAsync } from "../useAsync";

/** D1 "Launch mode" strip (DELIVERABLES v2.0 D1, D5; ADR-048.2): the tracked projects and briefs
 * whose launch-mode window is active now, from `launch_mode_window` via `/api/launch-mode`. */
export function LaunchModeList({ items }: { items: LaunchModeWindow[] }) {
  if (items.length === 0) return <p className="muted">No project is in launch mode.</p>;
  return (
    <ul>
      {items.map((w) => {
        const name = w.scope === "brief" ? `brief ${w.brief_id ?? "unknown"}` : (w.repo_full_name ?? w.repo_id ?? "unknown");
        const caseId = w.case_ids[0];
        return (
          <li key={w.id} className="launch-card">
            <span className="launch-name">{caseId ? <Link href={`/cases/${caseId}`}>{name}</Link> : name}</span>
            <span className="muted small">
              {w.source} · until {fmtTime(w.ends_at)}
            </span>
          </li>
        );
      })}
    </ul>
  );
}

export function LaunchModeStrip() {
  const lm = useAsync(() => api.launchMode(), "launch-mode");
  if (lm.status !== "ok") return null;
  return (
    <section className="launch-strip" aria-label="Launch mode">
      <h2>Launch mode</h2>
      <LaunchModeList items={lm.data.items} />
    </section>
  );
}
