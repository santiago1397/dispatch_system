/**
 * Chicago business-day (5am-cutoff) date math.
 *
 * The dispatch business operates 5:00 AM to midnight America/Chicago each
 * day. "Today" for reporting purposes means the current Chicago business
 * day, not the UTC calendar date — before 5am Chicago, we're still in
 * yesterday's business day. Mirrors the backend's `app.core.timezone`;
 * the two must stay in sync.
 */

const BUSINESS_TZ = "America/Chicago";
const BUSINESS_DAY_START_HOUR = 5;

/** Chicago wall-clock Y-M-D-H for an instant, via Intl (DST-safe). */
function chicagoParts(instant: Date): { y: number; m: number; d: number; h: number } {
  const fmt = new Intl.DateTimeFormat("en-US", {
    timeZone: BUSINESS_TZ,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    hour12: false,
  });
  const parts = Object.fromEntries(fmt.formatToParts(instant).map((p) => [p.type, p.value]));
  const hour = Number(parts.hour);
  return { y: Number(parts.year), m: Number(parts.month), d: Number(parts.day), h: hour === 24 ? 0 : hour };
}

/** "YYYY-MM-DD" business date for an instant: before 5am Chicago, it's still yesterday. */
export function businessDateIso(instant: Date): string {
  const { y, m, d, h } = chicagoParts(instant);
  const local = new Date(Date.UTC(y, m - 1, d));
  if (h < BUSINESS_DAY_START_HOUR) {
    local.setUTCDate(local.getUTCDate() - 1);
  }
  return local.toISOString().slice(0, 10);
}

/** Today's Chicago business date, "YYYY-MM-DD". */
export function todayBusinessIso(): string {
  return businessDateIso(new Date());
}
