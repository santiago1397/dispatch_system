/**
 * Types for the live per-company job status report. Mirrors
 * ``backend/app/schemas/company_report.py``.
 *
 * Unlike daily stats, this is computed on every request — no
 * precomputed snapshot table — so "today" is always current.
 */

export interface CompanyReportRow {
  company_id: string;
  company_name: string;
  rejected: number;
  closed_completed: number;
  scheduled_another_day: number;
  canceled: number;
  still_open: number;
  total: number;
}

export interface CompanyReportResponse {
  start_date: string;
  end_date: string;
  items: CompanyReportRow[];
}

/** Bucket keys reported per company — matches ``REPORT_BUCKETS`` on the backend. */
export type CompanyReportBucket = Exclude<
  keyof CompanyReportRow,
  "company_id" | "company_name" | "total"
>;

export interface CompanyReportJobRow {
  job_id: string;
  dispatch_job_id: string | null;
  bucket: CompanyReportBucket;
  lifecycle_status: string;
  /**
   * "arrival" if first_message_at put this job in range; "appointment" if
   * it only qualifies because appt_at lands in range (arrived on a
   * different day) — only meaningful when include_scheduled_appts was on.
   */
  matched_by: "arrival" | "appointment";
  first_message_at: string;
  appt_at: string | null;
  address: string | null;
  customer_name: string | null;
  customer_phone: string | null;
  job_type: string | null;
  message_preview: string | null;
}

export interface CompanyReportJobsResponse {
  start_date: string;
  end_date: string;
  company_id: string;
  company_name: string;
  /** One of ``CompanyReportBucket``, or ``"total"`` for the unfiltered "Total" column. */
  bucket: CompanyReportBucket | "total";
  items: CompanyReportJobRow[];
}
