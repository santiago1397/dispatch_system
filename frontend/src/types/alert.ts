/**
 * Types for the pipeline-health Alerts dashboard. Mirrors
 * ``backend/app/schemas/alert.py``.
 *
 * Each open alert represents a stuck / missing / unattributed job that
 * the operator should investigate. Resolved alerts are kept for audit
 * but hidden from the dashboard by default.
 */

export const ALERT_KINDS = [
  "stuck_dispatched",
  "stuck_in_progress",
  "appt_time_passed",
  "closing_missing",
  "dispatch_no_match",
  "unattributed_reply",
] as const;

export type AlertKind = (typeof ALERT_KINDS)[number];

export const ALERT_KIND_LABEL: Record<AlertKind, string> = {
  stuck_dispatched: "Stuck dispatched",
  stuck_in_progress: "Stuck in progress",
  appt_time_passed: "Appointment time passed",
  closing_missing: "Closing missing",
  dispatch_no_match: "Dispatch no match",
  unattributed_reply: "Unattributed reply",
};

export interface Alert {
  id: string;
  job_id: string | null;
  chat_jid: string | null;
  kind: AlertKind;
  threshold_minutes: number | null;
  detected_at: string;
  resolved_at: string | null;
  resolved_by_user_id: string | null;
  payload: Record<string, unknown>;
  created_at: string;
  updated_at: string | null;
}

export interface AlertList {
  items: Alert[];
  total: number;
}