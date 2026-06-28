/**
 * Types for the Jobs page (operator review of classified dispatches).
 * Mirrors `backend/app/schemas/dispatch_job.py`.
 */

export const DISPATCH_JOB_STATUSES = [
  "pending",
  "classified",
  "linked",
  "failed",
  "not_a_job",
  "closed",
  "closing_unmatched",
] as const;

export type DispatchJobStatus = (typeof DISPATCH_JOB_STATUSES)[number];

/**
 * Statuses the operator can filter by from the Jobs toolbar.
 *
 * Excludes ``not_a_job`` — the backend already hides those rows by
 * default (see ``exclude_statuses`` on GET /api/v1/dispatch/jobs), and
 * we don't want a way to surface them in the operator view.
 */
export const JOB_FILTER_STATUSES = [
  "pending",
  "classified",
  "linked",
  "failed",
  "closed",
  "closing_unmatched",
] as const;

export const DISPATCH_JOB_METHODS = [
  "phone",
  "regex",
  "ai",
  "dedup",
  "closing",
] as const;

export type DispatchJobMethod = (typeof DISPATCH_JOB_METHODS)[number];

export interface DispatchJob {
  id: string;
  incoming_message_id: string;
  source: "whatsapp" | "openphone" | null;
  company_id: string | null;
  company_name: string | null;
  job_id: string | null;
  classification_status: DispatchJobStatus;
  classification_method: DispatchJobMethod | null;
  classification_error: string | null;
  address: string | null;
  job_type: string | null;
  total: string | null;
  parts: string | null;
  payment_method: string | null;
  tech_name: string | null;
  car_make: string | null;
  car_model: string | null;
  car_year: string | null;
  customer_name: string | null;
  customer_phone: string | null;
  scheduled_at: string | null;
  job_description: string | null;
  /**
   * Tip and free-text notes from a closing message. Populated only when
   * this DispatchJob carries a closing (status = closed / closing_unmatched).
   * The other closing amounts (total, parts, payment_method) ride in
   * the standard columns above.
   */
  closing_tip: string | null;
  closing_notes: string | null;
  /**
   * Lifecycle pipeline status denormalized from the parent Job row.
   * Null only on legacy rows created before the lifecycle migration;
   * treat null as "pending" in the UI.
   */
  lifecycle_status: import("./lifecycle").LifecycleStatus | null;
  lifecycle_status_changed_at: string | null;
  created_at: string;
  updated_at: string | null;
}

export interface DispatchJobList {
  items: DispatchJob[];
  total: number;
}

export interface DispatchJobFilters {
  status?: DispatchJobStatus | null;
  company_id?: string | null;
  since?: string | null;
  until?: string | null;
  q?: string | null;
}

export const STATUS_LABEL: Record<DispatchJobStatus, string> = {
  pending: "Pending",
  classified: "Classified",
  linked: "Linked",
  failed: "Failed",
  not_a_job: "Not a job",
  closed: "Closed",
  closing_unmatched: "Closing — unmatched",
};

export const METHOD_LABEL: Record<DispatchJobMethod, string> = {
  phone: "Phone",
  regex: "Regex",
  ai: "AI",
  dedup: "Dedup",
  closing: "Closing",
};
