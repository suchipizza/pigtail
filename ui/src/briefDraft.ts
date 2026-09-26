// Path helpers for the D7 guided form. The form edits the brief JSON by dotted path; the server
// validates it with the same model as YAML import, so form and YAML are equivalent (R18.2).
import type { BriefData, FieldError } from "./api";

export function getPath(obj: unknown, path: string): unknown {
  let node: unknown = obj;
  for (const part of path.split(".")) {
    if (node === null || typeof node !== "object" || Array.isArray(node)) return undefined;
    node = (node as Record<string, unknown>)[part];
  }
  return node;
}

/** Immutable set; `undefined` removes the key (so optional fields fall back to defaults). */
export function setPath(obj: BriefData, path: string, value: unknown): BriefData {
  const [head, ...rest] = path.split(".");
  if (head === undefined) return obj;
  if (rest.length === 0) {
    if (value === undefined) return Object.fromEntries(Object.entries(obj).filter(([k]) => k !== head));
    return { ...obj, [head]: value };
  }
  const out: BriefData = { ...obj };
  const child = out[head];
  const base = child !== null && typeof child === "object" && !Array.isArray(child) ? (child as BriefData) : {};
  out[head] = setPath(base, rest.join("."), value);
  return out;
}

export function asString(v: unknown): string {
  return typeof v === "string" ? v : typeof v === "number" ? String(v) : "";
}

export function asList(v: unknown): string[] {
  return Array.isArray(v) ? v.map((x) => String(x)) : [];
}

/** One item per line; blank lines dropped. */
export function linesToList(text: string): string[] {
  return text
    .split("\n")
    .map((s) => s.trim())
    .filter((s) => s.length > 0);
}

/** "channel: band" lines ↔ a mapping (own audience per channel). */
export function linesToMap(text: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const line of linesToList(text)) {
    const i = line.indexOf(":");
    if (i <= 0) continue;
    out[line.slice(0, i).trim()] = line.slice(i + 1).trim();
  }
  return out;
}

export function mapToLines(v: unknown): string {
  if (v === null || typeof v !== "object" || Array.isArray(v)) return "";
  return Object.entries(v as Record<string, unknown>)
    .map(([k, x]) => `${k}: ${String(x)}`)
    .join("\n");
}

/** Errors for a field: exact path, or any error below it (e.g. `panel` shows `panel.winners`). */
export function errorsFor(errors: FieldError[], path: string): FieldError[] {
  return errors.filter((e) => e.path === path || e.path.startsWith(`${path}.`));
}

/** Values of a primitive for the form: numbers stay numbers, empty input removes the key. */
export function numberOrUndefined(text: string): number | undefined {
  if (text.trim() === "") return undefined;
  const n = Number(text);
  return Number.isFinite(n) ? n : undefined;
}
