// Typed client for the API (PRD R14.2; D7 briefs, M12). Shapes mirror src/pigtail/api/queries.py
// and src/pigtail/api/briefs.py.

/** One validation problem, naming the brief field (D7 acceptance). */
export interface FieldError {
  path: string;
  message: string;
}

export class ApiError extends Error {
  readonly status: number;
  readonly errors: FieldError[];
  constructor(status: number, message: string, errors: FieldError[] = []) {
    super(message);
    this.status = status;
    this.errors = errors;
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

/** A launch-mode window active now (D1 "Launch mode" strip; ADR-048.2, ADR-049.1). */
export interface LaunchModeWindow {
  id: number;
  scope: "brief" | "tracked_project";
  brief_id: string | null;
  repo_id: string | null;
  repo_full_name: string | null;
  case_ids: string[];
  starts_at: string;
  ends_at: string;
  source: "declared" | "detected" | "manual";
}

export interface LaunchMode {
  as_of: string;
  items: LaunchModeWindow[];
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

/** Case detail (D1). Detection numbers on older cases are shown as recorded: the hourly data
 * behind them was dropped with the global detection in M11 (ADR-047.6). */
export type CaseDetail = CaseDetailBase;

export interface CaseDetailBase {
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
  evidence_counts: Record<string, number>;
  caveats: string[];
  coded: boolean;
}

/** One star-history day (endpoint day label at 00:00 UTC; net of un-stars; ADR-032.3). */
export interface GithubPoint {
  t: string;
  stars_net: number;
  is_partial: boolean;
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
    star_history_days: { repo_host_id: number; day: string }[];
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
    let errors: FieldError[] = [];
    try {
      const body = (await res.json()) as { detail?: unknown; errors?: unknown };
      if (typeof body.detail === "string") msg = body.detail;
      if (Array.isArray(body.errors)) errors = body.errors as FieldError[];
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, msg, errors);
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
  launchMode: () => request<LaunchMode>("/api/launch-mode"),
  case: (id: string) => request<CaseDetail>(`/api/cases/${encodeURIComponent(id)}`),
  timeline: (id: string, q: Query) =>
    request<Timeline>(withQuery(`/api/cases/${encodeURIComponent(id)}/timeline`, q)),
  caseEvidence: (id: string, q: Query) =>
    request<EvidencePage>(withQuery(`/api/cases/${encodeURIComponent(id)}/evidence`, q)),
  evidence: (id: string) => request<EvidenceDetail>(`/api/evidence/${encodeURIComponent(id)}`),
  // D7 research briefs (M12). Writes are same-origin JSON POSTs, like the login.
  briefs: () => request<BriefList>("/api/briefs"),
  briefTemplate: () => request<{ brief: BriefData; yaml: string }>("/api/brief-template"),
  brief: (id: string) => request<BriefDetail>(`/api/briefs/${encodeURIComponent(id)}`),
  briefVersion: (id: string, v: number) =>
    request<StoredBriefView>(`/api/briefs/${encodeURIComponent(id)}/versions/${v}`),
  createBrief: (body: BriefWrite) => request<StoredBriefView>("/api/briefs", json(body)),
  saveBriefVersion: (id: string, body: BriefWrite) =>
    request<StoredBriefView & { created: boolean }>(`/api/briefs/${encodeURIComponent(id)}/versions`, json(body)),
  validateBrief: (body: BriefWrite) => request<BriefValidation>("/api/briefs/validate", json(body)),
  briefDiff: (id: string, from: number, to: number) =>
    request<BriefDiff>(withQuery(`/api/briefs/${encodeURIComponent(id)}/diff`, { from, to })),
  briefEstimate: (id: string, version?: number) =>
    request<Estimate>(withQuery(`/api/briefs/${encodeURIComponent(id)}/estimate`, { version })),
  /** R18.7: an LLM expansion proposal. Nothing is saved until the user saves the brief. */
  proposeExpansion: (id: string, body: { version?: number; approve_paid?: boolean }) =>
    request<ExpansionProposal>(`/api/briefs/${encodeURIComponent(id)}/expansion`, json(body)),
};

// --- D7 briefs --------------------------------------------------------------------------------

/** A brief as JSON (schemas/brief/v1.2.json). The form edits it by path; the server validates. */
export type BriefData = Record<string, unknown>;

export interface BriefWrite {
  brief?: BriefData;
  yaml?: string;
  base_version?: number;
  /** Save over the latest version when no base version is known (explicit opt-in). */
  force_latest?: boolean;
}

export interface ExpansionProvenance {
  job: "brief_expansion";
  prompt_id: string;
  prompt_version: string;
  prompt_fingerprint: string;
  model: string;
  backend: "subscription" | "api";
  input_hash: string;
  proposal_hash: string;
  generated_at: string;
  based_on_version?: number;
  edited_by_user: boolean;
}

/** R18.7 proposal: shown to the user, editable, never saved by the request that made it. */
export interface ExpansionProposal {
  label: "proposal";
  saved: false;
  brief_id: string;
  base_version: number;
  expansion: BriefData & { provenance?: ExpansionProvenance };
  cached: boolean;
  est_tokens: number;
  notes: string[];
}

export interface StoredBriefView {
  brief: BriefData;
  yaml: string;
  version: number;
  content_hash: string;
  warnings: string[];
}

export interface BriefVersionInfo {
  version: number;
  edited_at: string | null;
  supersedes: number | null;
  content_hash: string;
}

export interface BriefDetail extends StoredBriefView {
  versions: BriefVersionInfo[];
}

export interface BriefRunSummary {
  id: string;
  brief_version: number;
  status: string;
  created_at: string;
  finished_at: string | null;
  spend: Record<string, unknown>;
  stop: { kind: string; step: string; detail: string } | null;
}

export interface BriefListItem {
  brief_id: string;
  name: string;
  latest_version: number;
  versions: number;
  created_at: string | null;
  edited_at: string | null;
  status: string;
  last_run: BriefRunSummary | null;
  budget: { money_usd: number; subscription_share: number; llm_backend: string };
}

export interface BriefList {
  items: BriefListItem[];
  exposure_warning: string | null;
}

export interface BriefValidation {
  ok: true;
  brief: BriefData;
  yaml: string;
  warnings: string[];
}

export interface BriefChange {
  path: string;
  kind: "added" | "removed" | "changed";
  old: unknown;
  new: unknown;
  items_added?: unknown[];
  items_removed?: unknown[];
}

export interface BriefDiff {
  brief_id: string;
  from_version: number | null;
  to_version: number | null;
  changes: BriefChange[];
  unified: string;
}

export interface EstimateStage {
  stage: string;
  llm_stage: string | null;
  model: string | null;
  mode: "batch" | "standard";
  llm_calls: number;
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  cache_write_tokens: number;
  usd: number | null;
  reused: boolean;
}

export interface EstimateCap {
  cap_usd: number;
  spent_usd: number;
  remaining_usd: number;
  estimate_usd: number | null;
  within: boolean | null;
  field: string;
}

export interface Estimate {
  label: "estimate";
  model: string;
  github: { requests: Record<string, number>; hours_at_default_caps: number };
  other_requests: Record<string, number>;
  counts: { candidates: number; shortlisted: number; cases: number; exemplar_cases?: number };
  llm: {
    backend: string;
    calls: number;
    tokens: number;
    batch: boolean;
    stages: EstimateStage[];
    api_usd: number | null;
    per_llm_stage: Record<string, { model: string | null; calls: number; tokens: number; usd: number | null }>;
    pricing: { as_of: string; source: string; unit: string; batch_discount: number } | null;
    subscription_note: string | null;
  };
  caps: {
    brief: EstimateCap;
    month: EstimateCap;
    within_caps: boolean | null;
    on_exceed: string;
  };
  money: {
    usd: number | null;
    paid_steps: { step: string; source: string; est_usd: number | null; note: string }[];
    requires_approval: boolean;
  };
  reuse: { from_version: number | null; changed_fields: string[]; stages: { stage: string; action: string }[] } | null;
  expansion?: {
    status: "none" | "accepted_llm_proposal" | "written_by_user";
    edited_by_user: boolean;
    run_llm_calls: number;
    proposal: { llm_calls: number; input_tokens: number; output_tokens: number; command: string; note: string };
  };
}
