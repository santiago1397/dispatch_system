/**
 * Types for the Daily Stats dashboard. Mirrors
 * ``backend/app/schemas/daily_stats.py``.
 *
 * The backend writes one row per scope (``per_job`` / ``per_tech`` /
 * ``per_company``) for each date. Payload shape varies by scope — see
 * ``DailyStatsService.snapshot`` in the backend for the exact fields.
 */

export const STATS_SCOPES = ["per_job", "per_tech", "per_company"] as const;

export type StatsScope = (typeof STATS_SCOPES)[number];

export const STATS_SCOPE_LABEL: Record<StatsScope, string> = {
  per_job: "Per job",
  per_tech: "Per technician",
  per_company: "Per company",
};

export interface DailyStatsSnapshot {
  id: string;
  snapshot_date: string;
  scope: StatsScope;
  scope_id: string | null;
  payload: Record<string, unknown>;
  created_at: string;
  updated_at: string | null;
}

export interface DailyStatsList {
  items: DailyStatsSnapshot[];
  total: number;
}