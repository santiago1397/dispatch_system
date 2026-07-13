/**
 * Types for the pipeline-health Alerts dashboard. Mirrors
 * ``backend/app/schemas/alert.py``.
 *
 * Each open alert represents a stuck / missing / unattributed job that
 * the operator should investigate. Resolved alerts are kept for audit
 * but hidden from the dashboard by default.
 */

export const ALERT_KINDS = [
  "undispatched",
  "stuck_dispatched",
  "stuck_in_progress",
  "appt_time_passed",
  "follow_up_due",
  "company_update_unsent",
  "closing_missing",
  "closing_unfiled",
  "dispatch_no_match",
  "unattributed_reply",
  "tech_reply_no_target",
  "whatsapp_ingestion_stalled",
] as const;

export type AlertKind = (typeof ALERT_KINDS)[number];

export const ALERT_KIND_LABEL: Record<AlertKind, string> = {
  undispatched: "Undispatched",
  stuck_dispatched: "Stuck dispatched",
  stuck_in_progress: "Stuck in progress",
  appt_time_passed: "Appointment time passed",
  follow_up_due: "Follow-up due",
  company_update_unsent: "Update not relayed",
  closing_missing: "Closing missing",
  closing_unfiled: "Closing unfiled",
  dispatch_no_match: "Dispatch no match",
  unattributed_reply: "Unattributed reply",
  tech_reply_no_target: "Tech reply unmatched",
  whatsapp_ingestion_stalled: "WhatsApp not syncing",
};

/**
 * The parent Job an alert points at, resolved server-side. Mirrors
 * ``backend/app/schemas/alert.py::AlertJobSummary``. ``dispatch_job_id``
 * is what the operator-facing ``/jobs/{id}`` page is keyed by.
 */
export interface AlertJobSummary {
  job_id: string;
  dispatch_job_id: string | null;
  company_name: string | null;
  lifecycle_status: string | null;
  address: string | null;
  customer_name: string | null;
  customer_phone: string | null;
  job_type: string | null;
  message_preview: string | null;
  message_source: string | null;
}

export interface Alert {
  id: string;
  job_id: string | null;
  chat_jid: string | null;
  kind: AlertKind;
  threshold_minutes: number | null;
  detected_at: string;
  resolved_at: string | null;
  seen_at: string | null;
  resolved_by_user_id: string | null;
  payload: Record<string, unknown>;
  job: AlertJobSummary | null;
  created_at: string;
  updated_at: string | null;
}

export interface AlertList {
  items: Alert[];
  /** Unresolved count — the "unsolved" figure shown inside the dashboard. */
  total: number;
  /** Unresolved AND unseen — what the navbar badge shows. */
  unseen: number;
}