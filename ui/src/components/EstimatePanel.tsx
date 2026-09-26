// D7 "Run": the cost estimate shown before any run (R18.5, ADR-053.1). Always labelled an
// estimate. Starting runs arrives with M14; paid steps then need explicit approval.
import type { Estimate } from "../api";
import { fmtNum } from "../format";

export function EstimatePanel({ e }: { e: Estimate }) {
  const sub = e.llm.subscription;
  return (
    <section className="card" aria-label="Cost estimate">
      <h2>Cost estimate <span className="pill">estimate</span></h2>
      <p className="muted small">
        From the planning model <code>{e.model}</code>, not a measurement. Runs start from the CLI until M14.
      </p>
      <dl className="metrics">
        <div>
          <dt>GitHub requests (core / GraphQL / search)</dt>
          <dd>
            {fmtNum(e.github.requests.core ?? 0)} / {fmtNum(e.github.requests.graphql ?? 0)} /{" "}
            {fmtNum(e.github.requests.search ?? 0)}
          </dd>
        </div>
        <div>
          <dt>LLM calls</dt>
          <dd>{fmtNum(e.llm.calls)}</dd>
        </div>
        <div>
          <dt>LLM tokens</dt>
          <dd>{fmtNum(e.llm.tokens)}</dd>
        </div>
        {e.llm.backend === "subscription" ? (
          <div>
            <dt>Share of weekly subscription (cap {Math.round(sub.share_cap * 100)}%)</dt>
            <dd>
              {Math.round(sub.share_of_weekly_allowance * 100)}% · {sub.weeks} week(s)
            </dd>
          </div>
        ) : (
          <div>
            <dt>API cost (list price)</dt>
            <dd>${e.llm.api_usd.toFixed(2)}</dd>
          </div>
        )}
        <div>
          <dt>Money (paid services)</dt>
          <dd>{e.money.usd === null ? "unknown" : `$${e.money.usd.toFixed(2)}`}</dd>
        </div>
      </dl>
      <p className="muted small">
        Weekly allowance {fmtNum(sub.allowance.weekly_tokens)} tokens, basis: {sub.allowance.basis.replaceAll("_", " ")}.
      </p>
      {e.money.requires_approval && (
        <p className="error" role="alert">
          This run has paid steps ({e.money.paid_steps.map((p) => p.step).join(", ")}). It won&apos;t start without your
          explicit approval.
        </p>
      )}
      {e.reuse && (
        <p className="small">
          Re-run from v{e.reuse.from_version}: reused {e.reuse.stages.filter((s) => s.action.startsWith("reuse")).length}{" "}
          of {e.reuse.stages.length} stages; changed fields: {e.reuse.changed_fields.join(", ") || "none"}.
        </p>
      )}
    </section>
  );
}
