// Editable list of small objects (reference cases, distribution exemplars, competitors; brief
// schema v1.1, ADR-057). Each row is one object; "list" cells take whitespace-separated values.
import { useState } from "react";
import type { FieldError } from "../api";
import { asRows, cellText, errorsFor, setRowField, splitWords } from "../briefDraft";

export interface Column {
  key: string;
  label: string;
  kind?: "text" | "list";
  placeholder?: string | undefined;
}

interface Props {
  label: string;
  path: string;
  rows: unknown;
  columns: Column[];
  errors: FieldError[];
  onChange: (rows: Record<string, unknown>[]) => void;
  hint?: string | undefined;
  max?: number | undefined;
}

export function RowsEditor({ label, path, rows, columns, errors, onChange, hint, max = 40 }: Props) {
  const items = asRows(rows);
  const mine = errorsFor(errors, path);
  return (
    <div className="brief-field" role="group" aria-label={label}>
      <span>{label}</span>
      {hint && <span className="muted small">{hint}</span>}
      {items.length === 0 && <span className="muted small">none</span>}
      {items.map((row, i) => (
        <div className="brief-row" key={i} role="group" aria-label={`${label} ${i + 1}`}>
          {columns.map((c) => (
            <label key={c.key} className="brief-cell">
              <span className="small">{c.label}</span>
              <Cell
                label={`${label} ${i + 1} ${c.label}`}
                value={cellText(row, c.key)}
                list={c.kind === "list"}
                placeholder={c.placeholder}
                onText={(text) => onChange(items.map((r, j) => (j === i ? setRowField(r, c.key, text, c.kind) : r)))}
              />
            </label>
          ))}
          <button type="button" onClick={() => onChange(items.filter((_, j) => j !== i))}>
            Remove
          </button>
        </div>
      ))}
      {items.length < max && (
        <button type="button" onClick={() => onChange([...items, { name: "" }])}>
          Add
        </button>
      )}
      {mine.length > 0 && (
        <span className="error small" role="alert">
          {mine.map((e) => `${e.path}: ${e.message}`).join("; ")}
        </span>
      )}
    </div>
  );
}

/** A cell keeps the text as typed (so a separator can be typed in a "list" cell) while it still
 * matches the stored value; otherwise it shows the stored value. */
function Cell(props: { label: string; value: string; list: boolean; placeholder?: string | undefined; onText: (t: string) => void }) {
  const [typed, setTyped] = useState(props.value);
  const shown = props.list && splitWords(typed).join(" ") === props.value ? typed : props.value;
  return (
    <input
      aria-label={props.label}
      value={shown}
      placeholder={props.placeholder}
      onChange={(e) => {
        setTyped(e.target.value);
        props.onText(e.target.value);
      }}
    />
  );
}
