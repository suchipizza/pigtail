// D7 "Run": the cost estimate shown before any run (R18.5, R15.8-R15.11; ADR-064.4, ADR-072.4).
// Always labelled an estimate: USD per stage and model (Batch API and prompt caching included)
// against the brief's total cap and the monthly cap. Starting runs arrives later; paid steps
// then need explicit approval, and nothing is spent above a cap without the owner (H6).
import type { Estimate, EstimateCap } from "../api";
import { fmtNum } from "../format";

function usd(v: number | null | undefined): string {
  return v === null || v === undefined ? "unknown" : `$${v.toFixed(2)}`;
}

function CapRow({ label, c }: { label: string; c: EstimateCap }) {
  const verdict = c.within === null ? "unknown" : c.within ? "fits" : "exceeds the cap";
  return (
    <div>
      <dt>{label}</dt>
      <dd>
        {usd(c.cap_usd)} cap · {usd(c.spent_usd)} spent · {usd(c.remaining_usd)} left ·{" "}
        <span className={c.within === false ? "error" : undefined}>{verdict}</span>
      </dd>
    </div>
  );
}

export function EstimatePanel({ e }: { e: Estimate }) {
  const api = e.llm.backend === "api";
  const live = e.llm.stages.filter((s) => s.llm_calls > 0 && !s.reused);
  return (
    <section className="card" aria-label="Cost estimate">
      <h2>
        Cost estimate <span className="pill">estimate</span>
      </h2>
      <p className="muted small">
        From the planning model <code>{e.model}</code>, not a measurement. Runs start from the CLI for now.
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
        {api && (
          <div>
            <dt>API cost (list price{e.llm.batch ? ", Batch API" : ""}, prompt caching)</dt>
            <dd>{usd(e.llm.api_usd)}</dd>
          </div>
        )}
        <div>
          <dt>Money (API and paid services)</dt>
          <dd>{usd(e.money.usd)}</dd>
        </div>
      </dl>
      {api && live.length > 0 && (
        <table className="small" aria-label="Cost per stage">
          <thead>
            <tr>
              <th>Stage</th>
              <th>Model</th>
              <th>Mode</th>
              <th className="num">Calls</th>
              <th className="num">USD</th>
            </tr>
          </thead>
          <tbody>
            {live.map((s) => (
              <tr key={s.stage}>
                <td>{s.stage}</td>
                <td>
                  <code>{s.model}</code>
                </td>
                <td>{s.mode}</td>
                <td className="num">{fmtNum(s.llm_calls)}</td>
                <td className="num">{usd(s.usd)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <dl className="metrics" aria-label="Caps">
        <CapRow label="Brief cap (budget.money_usd, API included)" c={e.caps.brief} />
        <CapRow label="Monthly cap (BUDGET_USD_MONTH)" c={e.caps.month} />
      </dl>
      {e.caps.within_caps === false && (
        <p className="error" role="alert">
          This estimate exceeds a cap: the run would stop there with a resumable checkpoint. Spending above a cap needs
          the owner&apos;s approval (H6).
        </p>
      )}
      {e.llm.pricing && (
        <p className="muted small">
          Prices as of {e.llm.pricing.as_of} ({e.llm.pricing.unit}); batch requests cost{" "}
          {Math.round(e.llm.pricing.batch_discount * 100)}% of standard.
        </p>
      )}
      {e.llm.subscription_note && <p className="muted small">Subscription backend: {e.llm.subscription_note}.</p>}
      {e.money.requires_approval && (
        <p className="error" role="alert">
          This run has paid steps ({e.money.paid_steps.map((p) => p.step).join(", ")}). It won&apos;t start without your
          explicit approval.
        </p>
      )}
      {e.expansion && (
        <p className="muted small">
          Expansion (R18.7):{" "}
          {e.expansion.status === "none"
            ? "none yet"
            : e.expansion.status === "accepted_llm_proposal"
              ? `accepted model proposal${e.expansion.edited_by_user ? " (edited)" : ""}`
              : "written by you"}
          . The run makes no expansion call; a proposal is {e.expansion.proposal.llm_calls} call, about{" "}
          {fmtNum(e.expansion.proposal.input_tokens + e.expansion.proposal.output_tokens)} tokens, on demand.
        </p>
      )}
      {(e.counts.exemplar_cases ?? 0) > 0 && (
        <p className="muted small">
          Cases include {e.counts.exemplar_cases} for the distribution examples panel (each exemplar and its matched
          losers).
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
