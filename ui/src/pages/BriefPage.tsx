// D7 `/briefs/:id`: the current version, its versions with a diff between any two, and the cost
// estimate shown before a run (R18.4, R18.5), and the link to the shortlist review (M22, R4.7).
import { useState } from "react";
import { api } from "../api";
import { BriefDiffView } from "../components/BriefDiffView";
import { EstimatePanel } from "../components/EstimatePanel";
import { fmtTime } from "../format";
import { Link, useLocation } from "../router";
import { useAsync } from "../useAsync";

export function BriefPage({ id }: { id: string }) {
  const { search } = useLocation();
  const saved = search.get("saved");
  const detail = useAsync(() => api.brief(id), `brief:${id}:${saved ?? ""}`);
  const estimate = useAsync(() => api.briefEstimate(id), `estimate:${id}:${saved ?? ""}`);
  const [pair, setPair] = useState<{ from: number; to: number } | null>(null);
  const latest = detail.data?.version ?? 1;
  const from = pair?.from ?? Math.max(1, latest - 1);
  const to = pair?.to ?? latest;
  const diff = useAsync(() => api.briefDiff(id, from, to), `diff:${id}:${from}:${to}:${latest}`);

  if (detail.status === "error") {
    return (
      <p className="error" role="alert">
        {detail.error.message}
      </p>
    );
  }
  const d = detail.data;
  if (!d) return <p className="muted">Loading…</p>;
  const project = (d.brief.project ?? {}) as { name?: string; description?: string };
  const versions = d.versions.map((v) => v.version);

  return (
    <section>
      <p className="crumbs">
        <Link href="/briefs">Briefs</Link> / {id}
      </p>
      <h1>
        {project.name ?? id} <span className="muted">v{d.version}</span>
      </h1>
      {saved && (
        <p role="status">{saved === "unchanged" ? "No changes: no new version was created." : `Saved version ${d.version}.`}</p>
      )}
      <p>{project.description}</p>
      <div className="filters">
        <Link href={`/briefs/${id}/edit`}>Edit (creates a new version)</Link>
        <Link href={`/briefs/${id}/shortlist`}>Shortlist review</Link>
        <a href={`data:text/yaml;charset=utf-8,${encodeURIComponent(d.yaml)}`} download={`${id}.v${d.version}.yaml`}>
          Export YAML
        </a>
      </div>
      {d.warnings.length > 0 && (
        <ul className="muted small">
          {d.warnings.map((w) => (
            <li key={w}>{w}</li>
          ))}
        </ul>
      )}

      {estimate.data && <EstimatePanel e={estimate.data} />}
      {estimate.status === "error" && <p className="error">Estimate unavailable: {estimate.error.message}</p>}

      <h2>Versions</h2>
      <table className="data compact">
        <thead>
          <tr>
            <th>Version</th>
            <th>Saved</th>
            <th>Content hash</th>
          </tr>
        </thead>
        <tbody>
          {d.versions.map((v) => (
            <tr key={v.version}>
              <td>v{v.version}</td>
              <td>{fmtTime(v.edited_at)}</td>
              <td>
                <code>{v.content_hash.slice(0, 16)}</code>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <h2>Compare versions</h2>
      <div className="filters" role="group" aria-label="Compare versions">
        <label>
          From
          <select value={from} onChange={(e) => setPair({ from: Number(e.target.value), to })}>
            {versions.map((v) => (
              <option key={v} value={v}>
                v{v}
              </option>
            ))}
          </select>
        </label>
        <label>
          To
          <select value={to} onChange={(e) => setPair({ from, to: Number(e.target.value) })}>
            {versions.map((v) => (
              <option key={v} value={v}>
                v{v}
              </option>
            ))}
          </select>
        </label>
      </div>
      {diff.data && <BriefDiffView diff={diff.data} />}

      <details>
        <summary>YAML of v{d.version}</summary>
        <pre className="yaml">{d.yaml}</pre>
      </details>
    </section>
  );
}
