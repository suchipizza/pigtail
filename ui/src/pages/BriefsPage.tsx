// D7 `/briefs`: every brief in the install (R18.3), with version, status, last run and budget.
import { api, type BriefListItem } from "../api";
import { fmtTime } from "../format";
import { Link } from "../router";
import { useAsync } from "../useAsync";

function Row({ b }: { b: BriefListItem }) {
  const spend = b.last_run?.stop ? `stopped: ${b.last_run.stop.kind.replaceAll("_", " ")}` : "—";
  return (
    <tr>
      <td>
        <Link href={`/briefs/${b.brief_id}`}>{b.name}</Link>
        <div className="muted small">
          <code>{b.brief_id}</code>
        </div>
      </td>
      <td className="num">v{b.latest_version}</td>
      <td>
        <span className="pill">{b.status.replaceAll("_", " ")}</span>
      </td>
      <td>{b.last_run ? `${fmtTime(b.last_run.created_at)} (v${b.last_run.brief_version})` : "never run"}</td>
      <td>—</td>
      <td>
        ${b.budget.money_usd} total cap · {b.budget.llm_backend}
      </td>
      <td>{spend}</td>
    </tr>
  );
}

export function BriefsPage() {
  const list = useAsync(() => api.briefs(), "briefs");
  return (
    <section>
      <h1>Research briefs</h1>
      <p className="muted">
        Describe your project and what success means to you. Briefs stay in this instance&apos;s private data
        directory and never in git.
      </p>
      <div className="filters">
        <Link href="/briefs/new" className="button">
          New brief
        </Link>
        <Link href="/briefs/new?import=yaml">Import YAML</Link>
      </div>
      {list.status === "error" && (
        <p className="error" role="alert">
          {list.error.message}
        </p>
      )}
      {list.data?.exposure_warning && (
        <p className="error" role="alert">
          {list.data.exposure_warning}
        </p>
      )}
      {list.data && list.data.items.length === 0 && (
        <p>
          No briefs yet. <Link href="/briefs/new">Start from the synthetic example</Link>.
        </p>
      )}
      {list.data && list.data.items.length > 0 && (
        <table className="data">
          <thead>
            <tr>
              <th>Brief</th>
              <th className="num">Version</th>
              <th>Status</th>
              <th>Last run</th>
              <th>Next run</th>
              <th>Budget</th>
              <th>Spend / stop</th>
            </tr>
          </thead>
          <tbody>
            {list.data.items.map((b) => (
              <Row key={b.brief_id} b={b} />
            ))}
          </tbody>
        </table>
      )}
      {list.status === "loading" && !list.data && <p className="muted">Loading…</p>}
    </section>
  );
}
