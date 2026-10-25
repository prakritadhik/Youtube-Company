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
