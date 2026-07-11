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
