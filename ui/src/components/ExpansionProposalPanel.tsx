// D7 "LLM expansion" (R18.7): the model's proposal, editable before anything is saved. Accepting
// saves a new brief version with the expansion and its provenance; discarding saves nothing.
import { useState } from "react";
import type { BriefData, ExpansionProposal, FieldError } from "../api";
import { asList, asString, getPath, linesToList, setPath } from "../briefDraft";
import { RowsEditor } from "./RowsEditor";

const LISTS: [string, string][] = [
  ["users", "Users"],
  ["keywords", "Keywords"],
  ["topics", "Topics"],
  ["github_topics", "GitHub topics"],
  ["search_queries", "Search queries"],
];

interface Props {
  proposal: ExpansionProposal;
  errors: FieldError[];
  busy: boolean;
  /** Save a new brief version with this (edited) expansion. */
  onAccept: (expansion: BriefData) => void;
  /** Put it into the form without saving. */
  onUseInForm: (expansion: BriefData) => void;
  onDiscard: () => void;
}

export function ExpansionProposalPanel({ proposal, errors, busy, onAccept, onUseInForm, onDiscard }: Props) {
  const [exp, setExp] = useState<BriefData>(proposal.expansion);
  const prov = proposal.expansion.provenance;
  const set = (path: string, v: unknown) => setExp((cur) => setPath(cur, path, v));
  return (
    <section className="proposal" aria-label="Expansion proposal">
      <h2>
        Expansion proposal <span className="pill">proposal · not saved</span>
      </h2>
      <ul className="small">
        {proposal.notes.map((n) => (
          <li key={n}>{n}</li>
        ))}
      </ul>
      {prov && (
        <p className="muted small">
          {prov.model} via {prov.backend}, prompt {prov.prompt_id} v{prov.prompt_version} ({prov.prompt_fingerprint}),
          based on v{proposal.base_version}
          {proposal.cached ? ", served from the cache" : ""}.
        </p>
      )}
      <label className="brief-field">
        Problem statement
        <textarea
          rows={3}
          value={asString(getPath(exp, "problem_statement"))}
          onChange={(e) => set("problem_statement", e.target.value || undefined)}
        />
      </label>
      {LISTS.map(([key, label]) => (
        <label className="brief-field" key={key}>
          {label}
          <textarea
            rows={3}
            value={asList(getPath(exp, key)).join("\n")}
            onChange={(e) => set(key, linesToList(e.target.value))}
          />
        </label>
      ))}
      <RowsEditor
        label="Proposed competitors"
        path="expansion.competitors"
        rows={getPath(exp, "competitors")}
        columns={[
          { key: "name", label: "Name" },
          { key: "url", label: "URL (optional)" },
        ]}
        errors={errors}
        onChange={(next) => set("competitors", next)}
        hint="unverified model output: check each one"
      />
      <div className="filters">
        <button type="button" disabled={busy} onClick={() => onAccept(exp)}>
          Accept and save as new version
        </button>
        <button type="button" disabled={busy} onClick={() => onUseInForm(exp)}>
          Use in form (don&apos;t save yet)
        </button>
        <button type="button" disabled={busy} onClick={onDiscard}>
          Discard
        </button>
      </div>
    </section>
  );
}
