// D7 guided form (R18.1, R18.2). Mirrors schemas/brief/v1.json section by section; weights and
// per-dimension metrics sit under "Advanced". Validation happens on the server, and each error
// is shown next to the field it names.
import type { ReactNode } from "react";
import type { BriefData, FieldError } from "../api";
import {
  asList,
  asString,
  errorsFor,
  getPath,
  linesToList,
  linesToMap,
  mapToLines,
  numberOrUndefined,
  setPath,
} from "../briefDraft";

const DIMENSIONS = ["attention", "adoption", "community", "business"] as const;
const RANKABLE = ["attention", "adoption", "community"] as const;
const THRESHOLDS = ["none", "at_least_p25", "at_least_median", "top_third", "top_quartile", "top_decile"];
const BANDS = "none, r1 (< 1k), r2 (< 10k), r3 (< 100k), r4, unknown";
const STEPS = [
  "relax_primary_to_top_third",
  "drop_attention_minimum",
  "drop_adoption_minimum",
  "drop_community_minimum",
  "drop_business_minimum",
];
const SIGNALS = ["pricing_page", "hiring_hn_posts", "careers_roles"];
const SENSITIVITY = ["primary_swap", "band_shift", "weights", "fake_star_filter"];
const EXACT = ["founder_audience_bucket", "launch_half_year"];
const PAID = ["trendshift", "x", "bigquery"];

interface Props {
  draft: BriefData;
  errors: FieldError[];
  onChange: (next: BriefData) => void;
  /** The brief id can't change once a brief exists. */
  lockId?: boolean;
}

function FieldErrors({ errors, path }: { errors: FieldError[]; path: string }) {
  const mine = errorsFor(errors, path);
  if (mine.length === 0) return null;
  return (
    <span className="error small" role="alert">
      {mine.map((e) => `${e.path}: ${e.message}`).join("; ")}
    </span>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <fieldset className="brief-section">
      <legend>{title}</legend>
      {children}
    </fieldset>
  );
}

export function BriefForm({ draft, errors, onChange, lockId = false }: Props) {
  const set = (path: string, value: unknown) => onChange(setPath(draft, path, value));
  const str = (path: string) => asString(getPath(draft, path));

  const text = (path: string, label: string, opts: { area?: boolean; hint?: string; disabled?: boolean } = {}) => (
    <label className="brief-field">
      {label}
      {opts.area ? (
        <textarea value={str(path)} rows={3} onChange={(e) => set(path, e.target.value || undefined)} />
      ) : (
        <input value={str(path)} disabled={opts.disabled ?? false} onChange={(e) => set(path, e.target.value || undefined)} />
      )}
      {opts.hint && <span className="muted small">{opts.hint}</span>}
      <FieldErrors errors={errors} path={path} />
    </label>
  );

  const list = (path: string, label: string, hint = "one per line") => (
    <label className="brief-field">
      {label}
      <textarea
        rows={3}
        value={asList(getPath(draft, path)).join("\n")}
        onChange={(e) => set(path, linesToList(e.target.value))}
      />
      <span className="muted small">{hint}</span>
      <FieldErrors errors={errors} path={path} />
    </label>
  );

  const num = (path: string, label: string, attrs: { min?: number; max?: number; step?: number } = {}) => (
    <label className="brief-field">
      {label}
      <input
        type="number"
        value={str(path)}
        min={attrs.min}
        max={attrs.max}
        step={attrs.step ?? 1}
        onChange={(e) => set(path, numberOrUndefined(e.target.value))}
      />
      <FieldErrors errors={errors} path={path} />
    </label>
  );

  const select = (path: string, label: string, options: readonly string[], empty?: string) => (
    <label className="brief-field">
      {label}
      <select value={str(path)} onChange={(e) => set(path, e.target.value || undefined)}>
        {empty !== undefined && <option value="">{empty}</option>}
        {options.map((o) => (
          <option key={o} value={o}>
            {o}
          </option>
        ))}
      </select>
      <FieldErrors errors={errors} path={path} />
    </label>
  );

  const checks = (path: string, label: string, options: readonly string[]) => {
    const current = asList(getPath(draft, path));
    return (
      <div className="brief-field" role="group" aria-label={label}>
        <span>{label}</span>
        <div className="brief-checks">
          {options.map((o) => (
            <label key={o} className="inline">
              <input
                type="checkbox"
                checked={current.includes(o)}
                onChange={(e) =>
                  set(path, e.target.checked ? options.filter((x) => x === o || current.includes(x)) : current.filter((x) => x !== o))
                }
              />
              {o}
            </label>
          ))}
        </div>
        <FieldErrors errors={errors} path={path} />
      </div>
    );
  };

  const toggle = (path: string, label: string) => (
    <label className="inline">
      <input type="checkbox" checked={getPath(draft, path) === true} onChange={(e) => set(path, e.target.checked)} />
      {label}
    </label>
  );

  const minimum = (dim: (typeof RANKABLE)[number]) => {
    const path = `success.minimums.${dim}`;
    return (
      <label className="brief-field" key={dim}>
        Minimum on {dim}
        <select
          value={str(path)}
          onChange={(e) => {
            const current = getPath(draft, "success.minimums");
            const base = current !== null && typeof current === "object" ? (current as Record<string, unknown>) : {};
            const rest = Object.fromEntries(Object.entries(base).filter(([k]) => k !== dim));
            set("success.minimums", e.target.value ? { ...rest, [dim]: e.target.value } : rest);
          }}
        >
          <option value="">no minimum</option>
          {THRESHOLDS.filter((t) => t !== "none").map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </select>
        <FieldErrors errors={errors} path={path} />
      </label>
    );
  };

  const weights = getPath(draft, "success.weights");
  const weighted = weights !== null && typeof weights === "object";

  return (
    <div className="brief-form">
      <FieldErrors errors={errors} path="(brief)" />
      <Section title="Brief">
        {text("brief_id", "Brief id", { hint: "lowercase letters, digits and dashes", disabled: lockId })}
        {select("status", "Status", ["draft", "ready_for_discovery"])}
      </Section>

      <Section title="Project and target users">
        {text("project.name", "Project name")}
        {text("project.description", "Description", { area: true })}
        {text("project.target_users.primary", "Primary target users")}
        {text("project.target_users.secondary", "Secondary target users (optional)")}
        {text("project.business_model", "Business model")}
        {text("project.context", "Context (optional)", { area: true })}
      </Section>

      <Section title="Field boundaries">
        {text("field.core_field", "Core field", { hint: "findings for the core field are always reported separately (R4.10)" })}
        {list("field.include", "Include")}
        {list("field.exclude", "Exclude")}
        {list("field.seed_projects", "Seed projects", "names only; discovery proposes matches you confirm")}
        {list("field.reference_cases", "Reference cases", "kept in the report whatever their outcome (R4.11)")}
        {list("field.reference_models", "Reference models", "studied qualitatively, not in the panel")}
        {list("field.widening_steps", "Widening steps", "adjacent fields, in order, used only if the core field is too small")}
      </Section>

      <Section title="Time window">{num("window.months", "Months (12–18)", { min: 12, max: 18 })}</Section>

      <Section title="Success definition">
        {select("success.primary", "Primary dimension", DIMENSIONS, "choose…")}
        {select("success.primary_threshold", "Primary threshold", THRESHOLDS)}
        {RANKABLE.map(minimum)}
        {checks("success.business_minimum", "Required verified business signals", SIGNALS)}
        {select("success.fallbacks.no_measurable_adoption", "If a comparable has no measurable adoption", [
          "use_community_as_primary_and_flag",
          "fail_threshold",
          "skip_threshold",
        ])}
        {num("success.fallbacks.too_few_winners.min_winners", "Too few winners below", { min: 1, max: 25 })}
        {checks("success.fallbacks.too_few_winners.steps", "Then, in order", STEPS)}
        <details>
          <summary>Advanced: weights, metrics, sensitivity</summary>
          <p className="muted small">
            Weights only rank qualifiers inside this brief; results are always shown per dimension.
          </p>
          <label className="inline">
            <input
              type="checkbox"
              checked={weighted}
              onChange={(e) => set("success.weights", e.target.checked ? { attention: 0.34, adoption: 0.33, community: 0.33 } : undefined)}
            />
            Use weights
          </label>
          {weighted && RANKABLE.map((d) => <span key={d}>{num(`success.weights.${d}`, `Weight: ${d}`, { min: 0, max: 1, step: 0.01 })}</span>)}
          <FieldErrors errors={errors} path="success.weights" />
          {DIMENSIONS.map((d) => (
            <span key={d}>{text(`success.metrics.${d}`, `Metric for ${d} (optional)`)}</span>
          ))}
          {toggle("success.accept_self_reported", "Accept self-reported business values")}
          {checks("success.sensitivity", "Sensitivity alternatives", SENSITIVITY)}
        </details>
      </Section>

      <Section title="Your own audience">
        <label className="brief-field">
          Per channel (one &quot;channel: band&quot; per line)
          <textarea
            rows={4}
            value={mapToLines(getPath(draft, "own_audience.channels"))}
            onChange={(e) => set("own_audience.channels", linesToMap(e.target.value))}
          />
          <span className="muted small">Bands: {BANDS}. Only the band is stored.</span>
          <FieldErrors errors={errors} path="own_audience.channels" />
        </label>
        {text("own_audience.note", "Note (optional)")}
      </Section>

      <Section title="Channels and geography">
        {list("channels.planned", "Channels you can or want to use")}
        {list("channels.avoid", "Channels to avoid")}
        {text("geography.scope", "Geography")}
        {list("geography.languages", "Languages", "codes, e.g. en")}
      </Section>

      <Section title="Winners and losers">
        {num("panel.winners", "Winners (15–25)", { min: 15, max: 25 })}
        {num("panel.losers", "Losers (15–25)", { min: 15, max: 25 })}
        {checks("panel.exact_match", "Exact match on", EXACT)}
        {num("panel.smd_target", "Balance target (SMD)", { min: 0, max: 1, step: 0.05 })}
        {num("panel.headline_exclusion_smd", "Exclude pairs above (SMD)", { min: 0, max: 2, step: 0.05 })}
      </Section>

      <Section title="Budget">
        {num("budget.money_usd", "Money for paid services (USD)", { min: 0, step: 1 })}
        {num("budget.subscription_share", "Max share of weekly Claude subscription", { min: 0.05, max: 1, step: 0.05 })}
        {select("budget.llm_backend", "LLM backend (never switched automatically)", ["subscription", "api"])}
        {num("budget.llm_api_usd", "API spend cap (USD, api backend only)", { min: 0, step: 1 })}
        <div className="brief-field" role="group" aria-label="Optional sources">
          <span>Optional sources (off by default; paid steps need your approval)</span>
          {PAID.map((s) => (
            <span key={s}>{toggle(`optional_sources.${s}`, s)}</span>
          ))}
        </div>
      </Section>

      <Section title="Expansion (editable before saving)">
        {text("expansion.problem_statement", "Problem statement", { area: true })}
        {list("expansion.users", "Users")}
        {list("expansion.keywords", "Keywords")}
        {list("expansion.topics", "GitHub topics")}
        {list("expansion.competitors", "Competitors")}
      </Section>

      <Section title="Notes">{text("notes", "Notes (optional)", { area: true })}</Section>
    </div>
  );
}
