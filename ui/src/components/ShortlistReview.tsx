// D7 shortlist review (M22; PRD R4.7, R4.11): every candidate with its discovery sources, the
// relevance filter's verdict, reason, distance and rubric version, and the latest decision.
// Accept, reject (one or in bulk) and add by URL, always with a reason; precision against the
// 80 % target, shown even when below it; reference cases to confirm with their candidate repos.
import { useMemo, useState } from "react";
import type { Panel, ShortlistCandidate, ShortlistDecisionBody, ShortlistView, Verdict } from "../api";
import { fmtNum } from "../format";

export interface AddRequest {
  url: string;
  reason: string;
  panel?: Panel;
  resolves?: string;
}

interface Props {
  view: ShortlistView;
  busy?: boolean;
  onDecide: (body: ShortlistDecisionBody) => void;
  onAdd: (body: AddRequest) => void;
  onFinalize: () => void;
}

type VerdictFilter = "" | Verdict | "not_judged";

function pct(v: number | null): string {
  return v === null ? "n/a" : `${Math.round(v * 100)} %`;
}

function sourcesOf(c: ShortlistCandidate): string {
  return [...new Set(c.sources.map((s) => s.source))].join(", ");
}

function decisionText(c: ShortlistCandidate): string {
  if (c.decision) return `${c.decision.decision} (${c.decision.reviewer_role}): ${c.decision.reason}`;
  if (c.on_shortlist) return "on the shortlist (named in the brief)";
  if (c.proposed) return "proposed (model: relevant), undecided";
  return "undecided";
}

export function ShortlistReview({ view, busy = false, onDecide, onAdd, onFinalize }: Props) {
  const [verdict, setVerdict] = useState<VerdictFilter>("");
  const [panel, setPanel] = useState<"" | Panel>("");
  const [distance, setDistance] = useState<"" | "0" | "1" | "2">("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [reason, setReason] = useState("");
  const [addUrl, setAddUrl] = useState("");
  const [addPanel, setAddPanel] = useState<Panel>("field");
  const final = view.status === "final";
  const p = view.precision;
  const why = reason.trim();

  const rows = useMemo(
    () =>
      view.candidates.filter(
        (c) =>
          (verdict === "" || (c.verdict ?? "not_judged") === verdict) &&
          (panel === "" || c.panel === panel) &&
          (distance === "" || c.distance === Number(distance)),
      ),
    [view.candidates, verdict, panel, distance],
  );

  const toggle = (ref: string) => {
    const next = new Set(selected);
    if (next.has(ref)) next.delete(ref);
    else next.add(ref);
    setSelected(next);
  };
  const decide = (decision: "accept" | "reject", candidates: string[]) => {
    if (!why || candidates.length === 0) return;
    onDecide({ decision, reason: why, candidates });
    setSelected(new Set());
  };
  const bulk = (decision: "accept" | "reject", v: Verdict) => {
    if (!why) return;
    onDecide({ decision, reason: why, verdict: v });
  };
  const disabled = busy || final || !why;

  return (
    <div>
      <section className="card" aria-label="Review summary">
        <p>
          Status: <strong>{view.status === null ? "not started" : view.status === "final" ? "final" : "in review"}</strong> ·{" "}
          {fmtNum(view.counts.candidates)} candidates · on the shortlist {fmtNum(view.counts.on_shortlist)} · proposed{" "}
          {fmtNum(view.counts.proposed)} · undecided {fmtNum(view.counts.undecided)}
        </p>
        <p aria-label="Precision">
          Relevance-filter precision: <strong>{pct(p.value)}</strong> ({p.kept}/{p.decided} decided of {p.model_relevant} judged
          relevant; target {pct(p.target)}; {p.label})
          {p.meets_target === false && (
            <span className="error" role="alert">
              {" "}
              Below the {pct(p.target)} target.
            </span>
          )}
        </p>
        <p className="muted small">{p.definition}</p>
        {view.brief_warnings.length > 0 && (
          <details>
            <summary>Brief fields to confirm ({view.brief_warnings.length})</summary>
            <ul className="small">
              {view.brief_warnings.map((w) => (
                <li key={w}>{w}</li>
              ))}
            </ul>
          </details>
        )}
      </section>

      <div className="filters" role="group" aria-label="Filters">
        <label>
          Verdict
          <select value={verdict} onChange={(e) => setVerdict(e.target.value as VerdictFilter)}>
            <option value="">all</option>
            <option value="relevant">relevant</option>
            <option value="uncertain">uncertain</option>
            <option value="not_relevant">not relevant</option>
            <option value="not_judged">not judged</option>
          </select>
        </label>
        <label>
          Panel
          <select value={panel} onChange={(e) => setPanel(e.target.value as "" | Panel)}>
            <option value="">all</option>
            <option value="field">field</option>
            <option value="reference">reference</option>
            <option value="exemplar">exemplar</option>
          </select>
        </label>
        <label>
          Distance
          <select value={distance} onChange={(e) => setDistance(e.target.value as "" | "0" | "1" | "2")}>
            <option value="">all</option>
            <option value="0">0 (core)</option>
            <option value="1">1</option>
            <option value="2">2</option>
          </select>
        </label>
      </div>

      <div className="filters" role="group" aria-label="Decisions">
        <label>
          Reason (required for every decision)
          <input value={reason} onChange={(e) => setReason(e.target.value)} disabled={final} />
        </label>
        <button type="button" disabled={disabled || selected.size === 0} onClick={() => decide("accept", [...selected])}>
          Accept selected ({selected.size})
        </button>
        <button type="button" disabled={disabled || selected.size === 0} onClick={() => decide("reject", [...selected])}>
          Reject selected ({selected.size})
        </button>
        <button type="button" disabled={disabled} onClick={() => bulk("accept", "relevant")}>
          Accept all undecided relevant
        </button>
        <button type="button" disabled={disabled} onClick={() => bulk("reject", "uncertain")}>
          Reject all undecided uncertain
        </button>
      </div>

      <table className="data compact" aria-label="Candidates">
        <thead>
          <tr>
            <th aria-label="Select" />
            <th>Repository</th>
            <th className="num">Stars</th>
            <th>Found by</th>
            <th>Verdict</th>
            <th>Reason (model)</th>
            <th className="num">Dist.</th>
            <th>Panel</th>
            <th>Decision</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((c) => (
            <tr key={c.candidate_ref}>
              <td>
                <input
                  type="checkbox"
                  aria-label={`select ${c.repo_full_name ?? c.candidate_ref}`}
                  checked={selected.has(c.candidate_ref)}
                  onChange={() => toggle(c.candidate_ref)}
                  disabled={final}
                />
              </td>
              <td>
                {c.url ? (
                  <a href={c.url} rel="noreferrer noopener" target="_blank">
                    {c.repo_full_name}
                  </a>
                ) : (
                  c.candidate_ref
                )}
                {c.description && <div className="muted small">{c.description}</div>}
              </td>
              <td className="num">{c.stars === null ? "unknown" : fmtNum(c.stars)}</td>
              <td className="small">{sourcesOf(c)}</td>
              <td>{c.verdict ?? "not judged"}</td>
              <td className="small" title={c.rubric_version ?? undefined}>
                {c.reason ?? ""}
              </td>
              <td className="num">{c.distance ?? ""}</td>
              <td>{c.panel}</td>
              <td className="small">{decisionText(c)}</td>
              <td>
                <button type="button" disabled={disabled} onClick={() => decide("accept", [c.candidate_ref])}>
                  Accept
                </button>{" "}
                <button type="button" disabled={disabled} onClick={() => decide("reject", [c.candidate_ref])}>
                  Reject
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length === 0 && <p className="muted">No candidate matches these filters.</p>}

      <h2>Add a repository the filter missed</h2>
      <div className="filters" role="group" aria-label="Add by URL">
        <label>
          GitHub URL
          <input value={addUrl} onChange={(e) => setAddUrl(e.target.value)} placeholder="https://github.com/owner/name" disabled={final} />
        </label>
        <label>
          Panel
          <select value={addPanel} onChange={(e) => setAddPanel(e.target.value as Panel)} disabled={final}>
            <option value="field">field</option>
            <option value="reference">reference</option>
            <option value="exemplar">exemplar</option>
          </select>
        </label>
        <button
          type="button"
          disabled={disabled || !addUrl.trim()}
          onClick={() => {
            onAdd({ url: addUrl.trim(), reason: why, panel: addPanel });
            setAddUrl("");
          }}
        >
          Add
        </button>
      </div>

      <h2>Reference cases to confirm</h2>
      {view.reference_cases_to_confirm.length === 0 ? (
        <p className="muted">None: every named project resolved to a repo.</p>
      ) : (
        <ul aria-label="Reference cases to confirm">
          {view.reference_cases_to_confirm.map((r) => (
            <li key={r.candidate_ref}>
              <strong>{r.named_as ?? r.candidate_ref}</strong> <span className="muted small">({r.panel}; {r.resolution_rule})</span>
              {r.matches.length === 0 ? (
                <p className="muted small">No candidate repo found; add its URL above with this reference.</p>
              ) : (
                <ul>
                  {r.matches.map((m) => (
                    <li key={m.full_name}>
                      <a href={m.url} rel="noreferrer noopener" target="_blank">
                        {m.url}
                      </a>{" "}
                      <span className="muted small">
                        {m.stars === null ? "stars unknown" : `${fmtNum(m.stars)} stars`}
                        {m.description ? ` · ${m.description}` : ""}
                      </span>{" "}
                      <button
                        type="button"
                        disabled={disabled}
                        onClick={() => onAdd({ url: m.url, reason: why, resolves: r.candidate_ref })}
                      >
                        Use this repo
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </li>
          ))}
        </ul>
      )}

      <h2>Finalize</h2>
      <p className="muted small">
        Finalizing needs a decision on every relevant or uncertain candidate. Unresolved reference cases don't block it.
      </p>
      <button type="button" disabled={busy || final || view.counts.undecided > 0} onClick={onFinalize}>
        {final ? "Shortlist is final" : "Finalize the shortlist"}
      </button>
    </div>
  );
}
