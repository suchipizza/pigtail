// D7 version diff (R18.4): field-level changes plus the unified YAML diff.
import type { BriefDiff } from "../api";

function show(v: unknown): string {
  if (v === undefined) return "—";
  return JSON.stringify(v);
}

export function BriefDiffView({ diff }: { diff: BriefDiff }) {
  if (diff.changes.length === 0) {
    return (
      <p className="muted">
        v{diff.from_version} and v{diff.to_version} have the same content.
      </p>
    );
  }
  return (
    <>
      <table className="data compact">
        <caption>
          Changes from v{diff.from_version} to v{diff.to_version}
        </caption>
        <thead>
          <tr>
            <th>Field</th>
            <th>Change</th>
            <th>Before</th>
            <th>After</th>
          </tr>
        </thead>
        <tbody>
          {diff.changes.map((c) => (
            <tr key={c.path}>
              <td>
                <code>{c.path}</code>
              </td>
              <td>{c.kind}</td>
              <td>{show(c.old)}</td>
              <td>
                {show(c.new)}
                {c.items_added && c.items_added.length > 0 && (
                  <span className="muted small"> (+{c.items_added.map(String).join(", ")})</span>
                )}
                {c.items_removed && c.items_removed.length > 0 && (
                  <span className="muted small"> (−{c.items_removed.map(String).join(", ")})</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <details>
        <summary>YAML diff</summary>
        <pre className="yaml">{diff.unified}</pre>
      </details>
    </>
  );
}
