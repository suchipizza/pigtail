// Display helpers. All times are shown in UTC, like the data.

export function fmtTime(iso: string | null | undefined, withMinutes = true): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const s = d.toISOString();
  return `${s.slice(0, 10)} ${withMinutes ? s.slice(11, 16) : s.slice(11, 13) + "h"} UTC`;
}

export function fmtDay(iso: string): string {
  return new Date(iso).toISOString().slice(0, 10);
}

export function fmtNum(n: number | null | undefined, digits = 0): string {
  if (n === null || n === undefined) return "unknown";
  return n.toLocaleString("en-US", { maximumFractionDigits: digits, minimumFractionDigits: 0 });
}

export function shortHash(h: string): string {
  return `${h.slice(0, 10)}…`;
}

export const STATE_LABEL: Record<string, string> = {
  present: "present",
  raw_dropped: "raw dropped (hash kept)",
  deleted_upstream: "deleted upstream (hash kept)",
};

export const ROLE_LABEL: Record<string, string> = {
  case: "case",
  repo: "repo",
  hn_story: "HN story",
  hn_mention: "HN search",
  hn_mention_item: "HN item",
  hn_rank_poll: "HN rank poll",
  gharchive_hour: "GH Archive hour",
};

/** `datetime-local` input value (UTC) <-> ISO. */
export function toInputValue(iso: string): string {
  return new Date(iso).toISOString().slice(0, 16);
}
export function fromInputValue(v: string): string | null {
  if (!v) return null;
  const d = new Date(`${v}:00Z`);
  return Number.isNaN(d.getTime()) ? null : d.toISOString();
}
