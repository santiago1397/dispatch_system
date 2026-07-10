/**
 * Human-readable "why did this alert fire" text, derived from the alert
 * kind + its payload + threshold_minutes. Pure presentation — every value
 * used here is already present on the Alert the list endpoint returns, so
 * no extra fetch is needed.
 *
 * The alert engine (backend `app/services/alerts.py`) stamps context into
 * `payload` per kind: `since`, `appt_iso`, `follow_up_at`,
 * `lifecycle_status`, `update_kind`, `body_preview`. We format that into a
 * sentence rather than dumping raw JSON.
 */
import type { Alert } from "@/types";
import { formatDateTime, formatRelativeTime } from "@/lib/utils";

/** Read a string field out of the loosely-typed payload. */
function str(payload: Record<string, unknown>, key: string): string | null {
  const v = payload[key];
  return typeof v === "string" && v.length > 0 ? v : null;
}

/** "4h" / "45m" / "24h" from a minute count. */
export function formatDuration(minutes: number | null | undefined): string | null {
  if (minutes == null || minutes <= 0) return null;
  if (minutes < 60) return `${minutes}m`;
  const hours = minutes / 60;
  return Number.isInteger(hours) ? `${hours}h` : `${hours.toFixed(1)}h`;
}

/** Render a payload timestamp, tolerating free-text values (e.g. "tomorrow 3pm"). */
function when(iso: string | null): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : formatDateTime(d);
}

/**
 * A one-sentence explanation of why the alert triggered.
 */
export function describeAlert(alert: Alert): string {
  const p = alert.payload ?? {};
  const sla = formatDuration(alert.threshold_minutes);
  const slaSuffix = sla ? ` (SLA ${sla})` : "";
  const since = formatRelativeTime(str(p, "since"));

  switch (alert.kind) {
    case "undispatched":
      return `Job has been pending since ${since} with no dispatch or rejection${slaSuffix}.`;
    case "stuck_dispatched":
      return `Dispatched ${since} but the technician hasn't replied${slaSuffix}.`;
    case "stuck_in_progress":
      return `In progress since ${since} with no further update${slaSuffix}.`;
    case "appt_time_passed": {
      const appt = when(str(p, "appt_iso"));
      return appt
        ? `The scheduled appointment (${appt}) has passed and the job hasn't moved.`
        : `The scheduled appointment has passed and the job hasn't moved.`;
    }
    case "follow_up_due": {
      const at = when(str(p, "follow_up_at"));
      return at
        ? `A customer callback was due at ${at} and hasn't happened yet.`
        : `A customer callback is due and hasn't happened yet.`;
    }
    case "company_update_unsent": {
      const kind = str(p, "update_kind");
      return `A tech update${kind ? ` (${kind})` : ""} still hasn't been relayed to the source company${slaSuffix}.`;
    }
    case "closing_missing": {
      const status = str(p, "lifecycle_status");
      return `No closing totals have arrived since ${since}${status ? `; the job is still ${status}` : ""}${slaSuffix}.`;
    }
    case "closing_unfiled":
      return `The tech reported payment ${since}, but it hasn't been filed in the Dispatch Closing group yet${slaSuffix}.`;
    case "dispatch_no_match":
      return `An operator dispatch message couldn't be matched to any pending job.`;
    case "unattributed_reply":
      return `A technician reply matched more than one job, so it couldn't be attributed automatically.`;
    case "whatsapp_ingestion_stalled": {
      const lastMsg = when(str(p, "last_message_at"));
      return lastMsg
        ? `No WhatsApp messages have come in since ${lastMsg} — the scraper extension is likely disconnected.`
        : `No WhatsApp messages have ever been recorded — the scraper extension may not be connected.`;
    }
    default:
      return `Pipeline-health issue detected.`;
  }
}
