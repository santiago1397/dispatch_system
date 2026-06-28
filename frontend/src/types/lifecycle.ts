/**
 * Types for the Job lifecycle pipeline (Phase 1-3).
 * Mirrors `backend/app/schemas/job_lifecycle_event.py` and
 * `backend/app/services/lifecycle.py:LifecycleStatus`.
 *
 * The lifecycle pipeline tracks each Job through these states. Every
 * transition is append-only on the backend; the frontend reads the
 * current ``lifecycle_status`` denormalized on the Job row and the
 * audit-log events for the timeline.
 */

export const LIFECYCLE_STATUSES = [
  "pending",
  "dispatched",
  "in_progress",
  "appt_set",
  "needs_follow_up",
  "canceled",
  "completed",
  "closed",
] as const;

export type LifecycleStatus = (typeof LIFECYCLE_STATUSES)[number];

/** The statuses the operator can pick from the manual-override dropdown.
 *
 * Excludes ``closed`` — closing flows only through the CLOSING_CHAT_JID
 * WhatsApp group, never from this dropdown. The backend rejects it with
 * a 422 InvalidTransitionError, but the frontend hides the option too
 * so the operator never clicks it.
 */
export const MANUAL_LIFECYCLE_TRANSITIONS: LifecycleStatus[] = [
  "pending",
  "dispatched",
  "in_progress",
  "appt_set",
  "needs_follow_up",
  "canceled",
  "completed",
];

export const LIFECYCLE_STATUS_LABEL: Record<LifecycleStatus, string> = {
  pending: "Pending",
  dispatched: "Dispatched",
  in_progress: "In progress",
  appt_set: "Appt set",
  needs_follow_up: "Needs follow-up",
  canceled: "Canceled",
  completed: "Completed",
  closed: "Closed",
};

/**
 * Where a transition came from. The dropdown reads this on each event
 * so the operator can distinguish "I canceled it" from "the tech
 * replied with a cancel".
 */
export const LIFECYCLE_SOURCE_LABEL: Record<string, string> = {
  operator_whatsapp: "Operator dispatch",
  tech_whatsapp: "Tech reply",
  closing_chat: "Closing chat",
  manual: "Manual",
  ambiguous_attribution: "Ambiguous attribution",
};

export interface JobLifecycleEvent {
  id: string;
  job_id: string;
  source: string;
  from_status: string;
  to_status: string;
  payload: Record<string, unknown>;
  created_by_user_id: string | null;
  created_at: string;
  updated_at: string | null;
}

export interface JobLifecycleEventList {
  items: JobLifecycleEvent[];
  total: number;
}

/** Body for ``PATCH /jobs/{id}/lifecycle`` (manual override). */
export interface LifecycleTransitionInput {
  to_status: LifecycleStatus;
  note?: string | null;
}