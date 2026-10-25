export type Tone = "neutral" | "ok" | "warn" | "danger" | "info" | "accent";

export interface Risk { severity: string; text: string }

export interface Availability { available: boolean; reason?: string }

export interface ApprovalItem {
  id: number; kind: string; title: string; summary: string; why_it_matters: string;
  if_approved: string; if_rejected: string; evidence: { kind?: string; ref?: string; status?: string }[];
  status: string; requested_by: string; created_at: string; decided_by: string | null;
  decided_at: string | null; decision_note: string | null; subject_type: string | null;
}

export interface TaskSummary {
  id: number; title: string; task_type: string; status: string; priority: number;
  channel: string | null; owner: string; owner_slug: string | null; depends_on: number[];
  blocker: string | null; attempts: number; evidence_count: number; review_notes: string | null;
  created_at: string; updated_at: string; completed_at: string | null; board_version_used: number | null;
}

export interface GateIssue { code: string; message: string; waivable: boolean }
export interface VideoItem {
  id: number; channel: string | null; title: string; stage: string; stage_note: string | null;
  originality_status: string; ceo_rights_verified: boolean; asset_license_issues: number;
  qa_passed: boolean; medical_review_status: string; owner_approval_status: string;
  gate: { allowed: boolean; issues: GateIssue[] }; youtube_video_id: string | null;
}

export interface Costs {
  as_of: string; limits: Record<string, number>; note: string;
  today: { records: number; actual_usd: number; estimated_usd: number; included_in_subscription_units: number };
  month: { records: number; actual_usd: number; estimated_usd: number; included_in_subscription_units: number };
}

export interface Overview {
  company_status: { operating_mode: string; emergency_pause: boolean; label: string; real_publishing_enabled: boolean;
    pause_detail: { since: string; by: string; reason: string } | null };
  latest_executive_report: null | { id: number; created_at: string; headline: string;
    check_summary: { total: number; confirmed: number; contradicted: number; unverifiable: number };
    next_priorities: string[] };
  executive_report_note: string | null;
  pipeline: { in_production: number; awaiting_owner_approval: number; cleared_for_publishing: number; published: number;
    blocked_by_rights: number; total: number };
  blocked_tasks: TaskSummary[];
  approvals_pending: { count: number; items: ApprovalItem[] };
  risks: Risk[];
  costs: Costs;
  recent_verified_improvements: { public_id: string; title: string; status: string }[];
  metrics_availability: Record<string, Availability>;
  labels: Record<string, string>;
}

export interface BoardEntry {
  id: number; public_id: string; title: string; description: string; category: string; channel: string | null;
  status: string; confidence: string; risk_class: string; version: number; responsible_manager: string | null;
  review_date: string | null; test_plan: Record<string, string> | null; status_reason: string | null;
  created_by: string; created_at: string; updated_at: string;
  evidence?: { id: number; kind: string; ref: string; verification_status: string; note: string;
    verification_detail: string | null }[];
}
export interface BoardPoster {
  current_version: number; sections: Record<string, Record<string, BoardEntry[]>>;
  counts: Record<string, number>; releases: { version: number; reason: string; created_by: string; created_at: string }[];
  note: string;
}

export interface AgentNode {
  slug: string; name: string; level: "ceo" | "coo" | "manager" | "worker"; department: string | null;
  responsibilities: string; uses_ai_model: boolean; required_inputs: string[]; output_format: string[];
  acceptance_criteria: string[]; allowed_tools: string[]; reports_to: string | null;
  open_tasks: TaskSummary[]; actively_running: boolean;
}
export interface OrgView {
  departments: { slug: string; name: string; description: string }[];
  agents: AgentNode[]; system_tasks: TaskSummary[]; task_counts: Record<string, number>; note: string;
}

export interface ExecutiveReport {
  id: number; created_at: string; summary: string; board_version: number | null;
  content: {
    headline: string; generated_at: string; method: string; period: { start: string; end: string };
    company_status: Record<string, unknown>;
    sections: Record<string, Record<string, unknown>[]>;
    quiet_departments: { department: string; verified_zero_checks: number; statement: string }[];
    disagreements: Record<string, unknown>[]; risks: Risk[]; next_priorities: string[];
    check_summary: { total: number; confirmed: number; contradicted: number; unverifiable: number };
    department_coverage: { reported: string[]; missing: string[] };
    metrics_availability: Record<string, Availability>; legend: Record<string, string>;
  };
}

export interface AssetRow {
  id: number; kind: string; role: string; provider: string; model: string | null; seed: number | null;
  license_type: string; license_ref: string | null; commercial_use: string; attribution: string | null;
  simulated: boolean; size_bytes: number; problems: { code: string; message: string }[];
}
export interface VideoDetail extends VideoItem {
  channel_name: string | null; channel_policy: Record<string, boolean>; has_video_file: boolean; has_thumbnail: boolean;
  description: string | null; tags: string[]; honesty: string;
  brief: null | { version: number; status: string; created_by: string; reviewed_by: string | null; review_notes: string | null;
    board_version_used: number | null; content: Record<string, unknown> };
  text: null | { kind: string; version: number; author_kind: string; author: string; text: string; cited_claims: number };
  sources: { id: number; url: string; title: string; publisher: string; retrieved_at: string; supports: string }[];
  assets: AssetRow[];
  rights: null | { status: string; version: number; created_at: string; reviewed_by: string;
    findings: { severity: string; code: string; message: string }[];
    corrective_actions: { code: string; action: string; severity: string }[];
    distinctive_contributions: string[]; tools_used: string[]; limitations: string[]; status_basis: string };
  qa: null | { passed: boolean; version: number; created_at: string;
    checks: { id: string; name: string; status: string; detail: string; correction: string }[] };
  gate_for_publishing: { allowed: boolean; issues: GateIssue[] };
  tasks: TaskSummary[];
}

export interface ProviderCapability {
  id: string; description: string; implemented: boolean; verified_live: boolean; paid: boolean; state: string; detail: string; note: string;
}
export interface ProviderStatus {
  id: string; name: string; needs_key: boolean; key_configured: boolean | null; needs_attestation: boolean;
  attestation: null | { api_access_confirmed: boolean; commercial_use_confirmed: boolean; note: string; confirmed_at: string };
  capabilities: ProviderCapability[];
}

// --- Phase 3: research, planning and packaging ------------------------------------------------------------------
export interface Finding { code: string; severity: "block" | "revise" | "info"; field: string; message: string; waived?: string }

export interface ResearchNoteView {
  id: number; kind: string; url: string; title: string; publisher: string; method: string; retrieved_at: string; supports: string; note: string;
  excerpt: string; source_class: string; source_class_basis: string; figures: { label: string; value: string; as_of: string; source: string }[];
  injection_flags: string[]; untrusted: boolean; has_snapshot: boolean; promoted_source_id: number | null;
}

export interface IdeaSummary {
  id: number; channel_id: number; rank: number | null; title: string; concept: string; audience: string; status: string; origin: string;
  priority_score: number | null; assessed_at: string | null; planned_for: string | null; video_id: number | null; requires: string[];
  decision_note: string | null; decided_by: string | null;
  summary: { distinctness: string | null; feasibility: string | null; sources: number | null; demand: string | null };
}

export interface ScoreComponent { value: number | null; basis: string; detail: string; weight: number }

export interface IdeaDetail extends IdeaSummary {
  rationale: string; audience_relevance: number | null; risks: string[]; created_by: string; created_at: string; notes: ResearchNoteView[];
  assessment: {
    distinctness: { verdict: string; compared_against: number; basis: string;
      nearest: { kind: string; id: number; title: string; topic_overlap: number; title_similarity: number; verdict: string }[] };
    feasibility: { verdict: string; basis: string; note: string; blocking: string[];
      requirements: { requirement: string; label: string; state: string; ready: boolean; detail: string }[] };
    evidence: { sources: number; by_class: Record<string, number>; needs_sources: boolean; problems: string[]; flagged_notes: number;
      demand: { state: string; note: string } };
    priority: { score: number | null; ineligible: boolean; ineligible_reason: string; note: string; unavailable_components: string[];
      components: Record<string, ScoreComponent> };
    risks: string[]; automatic_risks: string[];
    estimates: { cost_usd: number | null; production_minutes: number | null; kind: string };
  };
}

export interface PlanResponse {
  channels: { channel: string; channel_name: string; items: IdeaSummary[] }[];
  requirements: { key: string; label: string }[]; note: string;
}

export interface MetadataSetView {
  id: number; video_id: number; version: number; status: string; chosen_title: string; generated_by: string; created_by: string;
  approved_by: string | null; approved_at: string | null; checked_at: string | null; worst_finding: string;
  finding_counts: { block: number; revise: number; info: number };
  title_candidates?: { title: string; source: string; label: string; findings: Finding[];
    criteria: Record<string, { state: string; detail: string }> }[];
  description?: string; tags?: string[]; hashtags?: string[]; chapters?: { start_s: number; title: string }[]; playlists?: string[];
  pinned_comment?: string; call_to_action?: string; recommendation?: string; findings?: Finding[];
}

export interface ConceptView {
  id: number; video_id: number; version: number; status: string; overlay_text: string; composition: string; palette: string[];
  relation_to_content: string; findings: Finding[]; created_by: string; approved_by: string | null; approved_at: string | null; note: string;
}

export interface Packaging {
  video_id: number; channel: string | null;
  metadata: { approved: MetadataSetView | null; latest: MetadataSetView | null; history: MetadataSetView[]; matches_video: boolean };
  thumbnail_concept: { approved: ConceptView | null; latest: ConceptView | null };
  chapters: { available: boolean; reason: string; timing: string; proposed: { start_s: number; title: string }[] };
  limits: Record<string, number>; limits_basis: string; gate_issues: { code: string; message: string }[]; unavailable: string[];
}

// --- Phase 4: YouTube --------------------------------------------------------------------------------------------
export interface YtConnection {
  status: string; google_channel_id?: string; google_channel_title?: string; google_channel_handle?: string | null; scopes?: string[];
  made_for_kids?: boolean | null; category_id?: string; category_name?: string | null; confirmed_by?: string | null; confirmed_at?: string | null;
  last_error?: string | null;
}
export interface YtStatus {
  oauth_client_configured: boolean; live_verified: boolean; note: string; operating_mode: string;
  channels: { channel: string; channel_name: string; connection: YtConnection }[];
  categories: Record<string, string>;
  upload_capability: { state: string; detail: string };
  quota: { used_today: number; daily_limit: number; remaining: number; resets_at: string; costs: Record<string, number>; basis: string };
}
export interface YtUpload {
  upload_id: number; status: string; youtube_video_id: string | null; privacy_requested: string; publish_at: string | null; thumbnail: string;
  thumbnail_detail: string | null; error: string | null; watch_url: string | null; task_id: number | null; bytes_total: number; bytes_sent: number;
  created_by: string; created_at: string; is_test: boolean; request: { title: string; made_for_kids: boolean; category_id: string };
  verification: { outcome?: string; checks?: { check: string; ok: boolean; expected: unknown; actual: unknown }[]; processing?: string | null; failure_reason?: string | null };
}

// --- Phase 7: operations ----------------------------------------------------------------------------------------
export interface OpsAlert {
  id: number; key: string; severity: "info" | "warning" | "critical"; title: string; detail: string; source: string; status: string; count: number;
  first_seen: string; last_seen: string; acknowledged_by: string | null; acknowledged_at: string | null;
}
export interface OpsSchedule {
  slug: string; title: string; description: string; cadence: { kind: string; every_minutes?: number; at?: string; days?: number[] }; enabled: boolean;
  catch_up: string; task_type: string; last_slot: string | null; last_run_at: string | null; last_task_id: number | null; next_run: string | null; due_now: boolean; timezone: string;
}
export interface CyclePhase { phase: string; status: string; summary: string; [k: string]: unknown }
export interface OpsStatus {
  worker: { alive: boolean; note: string; workers: { worker_id: string; host: string; pid: number; started_at: string; last_beat: string; seconds_since_beat: number; alive: boolean }[] };
  schedules: OpsSchedule[]; alerts: OpsAlert[]; alert_counts: { critical: number; warning: number; info: number; open: number };
  health: null | { checked_at: string; checks: { id: string; name: string; status: "ok" | "warn" | "fail"; detail: string }[] };
  daily_cycle: null | { ran_at: string; timezone: string; quiet: boolean; needs_attention: number; phases: CyclePhase[]; note?: string };
  timezone: string; operating_mode: string; notes: string[];
}
