// Typed client for the read-only API (PRD R14.2). Shapes mirror src/pigtail/api/queries.py.

export class ApiError extends Error {
  readonly status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export type CaseStatus = "live" | "pre_launch" | "closed";
export type DeletionState = "present" | "deleted_upstream" | "raw_dropped";
export type Reliability = "high" | "medium" | "low" | "unknown";

export interface SnapshotLink {
  available: boolean;
  href: string | null;
  state: DeletionState;
  in_store?: boolean | null;
}

export interface EvidenceRecord {
  id: string;
  source: string;
  url: string | null;
  fetched_at: string;
  content_hash: string;
  content_type: string | null;
  http_status: number | null;
  reliability: Reliability;
  terms_basis: string;
  retention_class: string;
  deletion_state: DeletionState;
  collector_version: string;
  case_id: string | null;
  repo_id: string | null;
  run_id: string | null;
  snapshot: SnapshotLink;
  roles?: string[];
}

export interface CaseSummary {
  id: string;
  repo_id: string;
  repo_full_name: string;
  opened_at: string;
  closed_at: string | null;
  trigger: string;
  status: CaseStatus;
  stars_48h: number | null;
  z_score: number | null;
  detected_hour: string | null;
  baseline_quality: string | null;
  coverage_ratio: number | null;
  evidence_linked: number;
}

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface Coverage {
  source: string;
  window_start: string;
  window_end: string;
  observed_stars: number;
  reference_stars: number | null;
  reference_source: string | null;
  ratio: number | null;
}

export interface Detection {
  rule_version: string;
  detected_hour: string;
  stars_48h: number;
  stars_48h_raw: number;
  forks_48h: number;
  baseline_mean_48h: number;
  baseline_std_48h: number;
  sigma_used: number;
  z_score: number;
  baseline_hours_covered: number;
  baseline_quality: string;
  threshold_min_stars: number;
  threshold_sigma: number;
  bot_filter_version: string;
  coverage: Coverage;
}

export interface DetectionHour {
  hour: string;
  scan_status: "ok" | "missing" | null;
  evidence_id: string | null;
  stars_raw: number;
  stars_filtered: number;
  forks_filtered: number;
  lockstep: boolean;
}

export interface CaseDetail {
  case: {
    id: string;
    repo_id: string;
    opened_at: string;
    closed_at: string | null;
    trigger: string;
    status: CaseStatus;
    run_id: string | null;
  };
  repo: { id: string; host: string; host_id: number; full_name: string; first_seen_at: string };
  detection: Detection | null;
  coverage: Coverage | null;
  detection_hours: DetectionHour[];
  evidence_counts: Record<string, number>;
  caveats: string[];
  coded: boolean;
}

export interface GithubPoint {
  t: string;
  hours_observed: number;
  hours_missing: number;
  stars_raw: number | null;
  stars_filtered: number | null;
  stars_bot: number | null;
  stars_lockstep: number | null;
  forks_raw: number | null;
  forks_filtered: number | null;
  lockstep: boolean;
  evidence_ids: string[];
}

export interface HnStory {
  item_id: number;
  title: string | null;
  url: string | null;
  created_at: string | null;
  score: number | null;
  best_rank: number | null;
  deleted: boolean;
  dead: boolean;
  evidence_id: string | null;
}

export interface HnRank {
  item_id: number;
  t: string;
  best_rank: number;
  score: number | null;
  observations: number;
  observed_at: string;
  evidence_id: string | null;
}

export interface HnMention {
  item_id: number;
  item_type: string;
  created_at: string | null;
  story_id: number | null;
  points: number | null;
  num_comments: number | null;
  title: string | null;
  match_kind: string;
  show_hn: boolean;
  ask_hn: boolean;
  front_page_tag: boolean;
  evidence_id: string | null;
  item_evidence_id: string | null;
}

export interface TimelineEvent {
  t: string;
  kind: "evidence";
  roles: string[];
  evidence: EvidenceRecord;
}

export type Bucket = "hour" | "day";

export interface Timeline {
  case_id: string;
  range: { from: string; to: string; bucket: Bucket };
  github: GithubPoint[];
  hn: { stories: HnStory[]; ranks: HnRank[]; mentions: HnMention[] };
  events: TimelineEvent[];
  caveats: string[];
  coded: boolean;
}

export interface EvidencePage extends Page<EvidenceRecord> {
  sources: string[];
  roles: string[];
}

export interface EvidenceDetail {
  evidence: EvidenceRecord;
  links: {
    case_id: string | null;
    repo_id: string | null;
    gharchive_hours: { hour: string; status: string }[];
    hn_rank_polls: string[];
    hn_stories: { item_id: number; repo_id: string | null }[];
    hn_mentions: { item_id: number; repo_id: string | null; case_id: string | null }[];
    same_bytes_evidence_ids: string[];
  };
  retention: { retention_class: string; deletion_state: string; rule: string; raw_drop_due_at: string | null };
}

type Query = Record<string, string | number | undefined | null>;

export function withQuery(path: string, query: Query = {}): string {
  const params = new URLSearchParams();
  for (const [k, v] of Object.entries(query)) {
    if (v !== undefined && v !== null && v !== "") params.set(k, String(v));
  }
  const qs = params.toString();
  return qs ? `${path}?${qs}` : path;
}

let onUnauthorized: () => void = () => undefined;
export function setUnauthorizedHandler(fn: () => void): void {
  onUnauthorized = fn;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, { credentials: "same-origin", ...init });
  if (res.status === 401 && !path.startsWith("/api/auth/")) onUnauthorized();
  if (!res.ok) {
    let msg = res.statusText;
    try {
      const body = (await res.json()) as { detail?: unknown };
      if (typeof body.detail === "string") msg = body.detail;
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, msg);
  }
  return (await res.json()) as T;
}

const json = (body: unknown): RequestInit => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export const api = {
  me: () => request<{ authenticated: boolean }>("/api/auth/me"),
  login: (password: string) => request<{ authenticated: boolean }>("/api/auth/login", json({ password })),
  logout: () => request<{ authenticated: boolean }>("/api/auth/logout", json({})),
  cases: (q: Query) => request<Page<CaseSummary>>(withQuery("/api/cases", q)),
  case: (id: string) => request<CaseDetail>(`/api/cases/${encodeURIComponent(id)}`),
  timeline: (id: string, q: Query) =>
    request<Timeline>(withQuery(`/api/cases/${encodeURIComponent(id)}/timeline`, q)),
  caseEvidence: (id: string, q: Query) =>
    request<EvidencePage>(withQuery(`/api/cases/${encodeURIComponent(id)}/evidence`, q)),
  evidence: (id: string) => request<EvidenceDetail>(`/api/evidence/${encodeURIComponent(id)}`),
};
