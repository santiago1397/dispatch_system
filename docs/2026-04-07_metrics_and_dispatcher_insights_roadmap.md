# Metrics & Dispatcher Insights Roadmap

**Created:** 2026-04-07
**Status:** Planning

## Current State

The project has a working classification pipeline that processes incoming OpenPhone messages through a 3-tier system (phone match -> regex -> AI) and extracts 9 structured fields per job. 38 companies are seeded with identification patterns. However, **zero analytics or dispatcher-facing tooling exists today**:

- No aggregation queries beyond basic list/count with filters
- No time-based reporting (daily, weekly, monthly)
- No dashboard pages in the frontend
- No real-time shift monitoring
- No company-level insights or scorecards
- Prometheus only tracks HTTP-level metrics (latency, request counts)
- The AI chat agent has no access to dispatch data

---

## Objective 1: Reports & Metrics on Collected Data

### What data we have

| Source | Fields | Volume |
|--------|--------|--------|
| `incoming_messages` | from_number, to_numbers, content, event_type, raw_payload | Every webhook from OpenPhone |
| `dispatch_jobs` | company_id, status, method, address, job_type, total, parts, payment_method, tech_name, car_make/model/year | Classified subset of messages |
| `companies` | name, display_name, phone_numbers, patterns | 38 companies seeded |
| `classification_status` enum | pending, classified, failed, not_a_job | Per-job state |

### Phase 1: Aggregation Queries & API Endpoints

**New file: `backend/app/repositories/analytics.py`**

Queries needed (all async, SQLAlchemy + `func`):

```
Daily job counts by status
  - GROUP BY DATE(created_at), classification_status
  - For "today", "last 7 days", "last 30 days", YTD

Jobs per company (with time range)
  - GROUP BY company_id, DATE(created_at)
  - Includes company name join

Classification method breakdown
  - GROUP BY classification_method
  - Ratio of phone vs regex vs AI identification

Failed classification analysis
  - Filter status = 'failed' or 'not_a_job'
  - Include classification_error and sample messages

Revenue proxy (total field)
  - SUM/AVG of parsed total amounts by company, by day
  - NOTE: `total` is currently a String(50) -- needs parsing logic

Job type distribution
  - GROUP BY job_type

Payment method distribution
  - GROUP BY payment_method

Peak hours analysis
  - GROUP BY EXTRACT(HOUR FROM created_at)
  - Shows message volume by hour of day

Tech performance
  - GROUP BY tech_name
  - Job count, revenue proxy, by time range
```

**New file: `backend/app/schemas/analytics.py`**

Pydantic response models:

```python
DailyMetricsResponse
  - date: date
  - total_messages: int
  - total_jobs: int
  - classified: int
  - failed: int
  - not_a_job: int
  - pending: int

CompanyMetricsResponse
  - company_id: UUID
  - company_name: str
  - job_count: int
  - avg_total: float | None
  - job_types: dict[str, int]
  - last_job_at: datetime | None

DashboardSummaryResponse
  - date_range: DateRange
  - daily_metrics: list[DailyMetricsResponse]
  - top_companies: list[CompanyMetricsResponse]
  - classification_methods: dict[str, int]
  - peak_hours: list[tuple[int, int]]  # (hour, count)
  - payment_methods: dict[str, int]
  - job_types: dict[str, int]
  - total_revenue_estimate: float | None

YTDReportResponse
  - year: int
  - monthly_breakdown: list[MonthlyMetricsResponse]
  - companies_active: int
  - companies_inactive: list[str]  # companies with 0 jobs YTD
  - top_techs: list[TechMetricsResponse]
  - collection_gaps: list[DateRange]  # dates with 0 messages
```

**New file: `backend/app/services/analytics.py`**

Service layer that:
- Validates date ranges
- Calls repository aggregation functions
- Computes derived metrics (trends, comparisons to previous period)
- Parses the string `total` field into numeric values for revenue estimates
- Identifies data collection gaps (days with zero incoming messages)

**New file: `backend/app/api/routes/v1/analytics.py`**

Endpoints:

| Method | Path | Description |
|--------|------|-------------|
| GET | `/analytics/dashboard` | Summary with configurable date range (default: last 7 days) |
| GET | `/analytics/daily` | Daily breakdown for a date range |
| GET | `/analytics/companies` | Per-company metrics with optional time filter |
| GET | `/analytics/company/{id}` | Deep dive on a single company |
| GET | `/analytics/ytd` | Year-to-date report with monthly breakdown |
| GET | `/analytics/gaps` | Dates/intervals with no data collection |
| GET | `/analytics/export/csv` | CSV export of filtered job data |

All endpoints require JWT auth. Admin-only for YTD and export.

### Phase 2: Scheduled Reports

**Enhancement to `backend/app/commands/` or a new background worker:**

- Daily summary report generated at end of day (configurable time)
- Weekly rollup every Monday morning
- Output formats: stored in DB for dashboard retrieval, optional email/Slack push
- New model: `Report` (type, date_range, generated_at, payload JSONB, pdf_url)

### Phase 3: Frontend Dashboard Pages

**New pages in `frontend/src/app/(dashboard)/`:**

```
/analytics
  page.tsx              -- Dashboard overview (KPI cards + charts)
  /daily/page.tsx       -- Daily breakdown table/charts
  /companies/page.tsx   -- Company leaderboard + drill-down
  /company/[id]/page.tsx -- Single company deep dive
  /ytd/page.tsx         -- Year-to-date report view
  /gaps/page.tsx        -- Data collection gap analysis
```

**Charts library:** Recharts or Chart.js (via react-chartjs-2)

Key visualizations:
- Stacked bar chart: daily jobs by status
- Line chart: daily message volume over time
- Pie/donut: classification method breakdown
- Bar chart: top 10 companies by job count
- Heatmap: message volume by hour/day-of-week
- Table: company scorecard with sorting

---

## Objective 2: Dispatcher Shift Insights

### What dispatchers need

During a shift, a dispatcher needs to know:

1. **What's happening right now** -- recent jobs, open issues, anomalies
2. **Company activity** -- which companies are active, which are silent
3. **Performance signals** -- unusually high/low volume, failed classifications
4. **Historical context** -- "is today busier than usual?" compared to same day/week

### Phase 1: Real-Time Shift API

**New file: `backend/app/repositories/shift.py`**

Queries:
```
Active shift summary
  - Jobs in last 8 hours (configurable shift length)
  - Breakdown by status, company, job_type
  - Comparison to same window yesterday and same day last week

Recent activity feed
  - Last N classified jobs with company name + extracted fields
  - Ordered by created_at desc

Company shift status
  - Per-company: jobs this shift vs average, last seen (most recent message)
  - Flag companies that are unusually quiet or active

Anomaly detection
  - Compare current hourly rate to rolling 7-day average
  - Flag if > 2 standard deviations from mean
  - Failed classification spike detection
```

**New file: `backend/app/schemas/shift.py`**

```python
ShiftSummaryResponse
  - shift_start: datetime
  - shift_duration_hours: float
  - jobs_this_shift: int
  - jobs_vs_yesterday: float  # percentage change
  - jobs_vs_last_week: float
  - active_companies: list[CompanyShiftStatus]
  - recent_jobs: list[DispatchJobRead]  # last 20
  - alerts: list[ShiftAlert]

CompanyShiftStatus
  - company_id: UUID
  - company_name: str
  - jobs_this_shift: int
  - jobs_avg_shift: float  # rolling average
  - last_message_at: datetime | None
  - status: "active" | "quiet" | "unusual"

ShiftAlert
  - type: "spike" | "drop" | "failures" | "new_company" | "silent_company"
  - severity: "info" | "warning" | "critical"
  - message: str
  - company_name: str | None
  - detected_at: datetime
```

**New file: `backend/app/services/shift.py`**

Business logic:
- Determine shift window (configurable start time, default 8 hours rolling)
- Compute comparison metrics (vs yesterday, vs same-day-last-week)
- Apply anomaly thresholds
- Generate alerts based on configurable rules

**New file: `backend/app/api/routes/v1/shift.py`**

| Method | Path | Description |
|--------|------|-------------|
| GET | `/shift/summary` | Current shift overview with comparisons |
| GET | `/shift/activity` | Recent job activity feed (paginated) |
| GET | `/shift/companies` | Company activity status for current shift |
| GET | `/shift/alerts` | Active alerts and anomalies |
| WS | `/ws/shift` | Real-time shift updates (new job triggers push) |

### Phase 2: Dispatcher AI Assistant

**Enhancement to `backend/app/agents/`:**

Upgrade the existing LangChain agent with dispatch-specific tools:

**New tools (`backend/app/agents/tools/`):**

```python
get_shift_summary_tool
  - Calls shift service, returns natural language summary

get_company_status_tool
  - Takes company name, returns recent activity + metrics

get_recent_jobs_tool
  - Takes optional filters (company, status, time range)

compare_periods_tool
  - "Compare today to last Monday"
  - Returns percentage changes across key metrics

get_company_insights_tool
  - Takes company name, returns:
    - Average jobs per day/week
    - Common job types
    - Payment method trends
    - Typical operating hours
    - Revenue estimate trends
```

**Updated system prompt (`backend/app/agents/prompts.py`):**

```
You are a dispatch operations assistant for a Chicago locksmith dispatch center.
You have real-time access to shift data, company metrics, and job classifications.
Help dispatchers understand what's happening, spot trends, and make decisions.
When asked about "today" or "this shift", use the shift tools.
When asked about a company, use the company insights tool.
Always provide actionable context, not just numbers.
```

This means the existing `/ws/agent` WebSocket endpoint already works -- just add tools and update the prompt.

### Phase 3: Dispatcher Frontend Pages

**New pages:**

```
/shift
  page.tsx              -- Live shift dashboard
                        - KPI bar: jobs count, vs yesterday, vs last week
                        - Company activity grid (green/yellow/red status)
                        - Recent jobs feed (auto-refreshing)
                        - Alerts panel

/company/[id]
  page.tsx              -- Company deep dive
                        - Profile card (name, phone numbers, patterns)
                        - Activity timeline
                        - Job breakdown charts
                        - Historical trends
```

**WebSocket integration:**
- Connect to `/ws/shift` for live updates
- Show toast notifications for new alerts
- Auto-refresh activity feed

---

## Implementation Priority

### Priority 1 -- Foundation (Backend Analytics)

| Step | Files | Effort |
|------|-------|--------|
| 1.1 | `schemas/analytics.py` | Schemas for all analytics responses |
| 1.2 | `repositories/analytics.py` | Aggregation queries |
| 1.3 | `services/analytics.py` | Analytics business logic + total parsing |
| 1.4 | `api/routes/v1/analytics.py` | Analytics endpoints |
| 1.5 | Register router in `api/routes/v1/__init__.py` | Wire up |
| 1.6 | New Alembic migration if needed | Schema changes |

### Priority 2 -- Dispatcher Shift API

| Step | Files | Effort |
|------|-------|--------|
| 2.1 | `schemas/shift.py` | Shift response schemas |
| 2.2 | `repositories/shift.py` | Shift aggregation queries |
| 2.3 | `services/shift.py` | Shift logic + alert rules |
| 2.4 | `api/routes/v1/shift.py` | Shift endpoints + WebSocket |
| 2.5 | Register router | Wire up |

### Priority 3 -- AI Agent Upgrade

| Step | Files | Effort |
|------|-------|--------|
| 3.1 | `agents/tools/shift_tools.py` | Shift summary + activity tools |
| 3.2 | `agents/tools/company_tools.py` | Company status + insights tools |
| 3.3 | `agents/tools/analytics_tools.py` | Period comparison + metrics tools |
| 3.4 | `agents/prompts.py` | Updated system prompt |
| 3.5 | `agents/langchain_assistant.py` | Register new tools |

### Priority 4 -- Frontend Dashboards

| Step | Files | Effort |
|------|-------|--------|
| 4.1 | Install chart library (recharts) | Dependency |
| 4.2 | `/analytics` page | Dashboard overview |
| 4.3 | `/analytics/companies` | Company leaderboard |
| 4.4 | `/shift` page | Live shift dashboard |
| 4.5 | `/company/[id]` page | Company deep dive |
| 4.6 | `/analytics/ytd` | Year-to-date view |
| 4.7 | `/analytics/gaps` | Data gap analysis |

---

## Data Gaps to Address

### String `total` field

The `total` column is `String(50)`. For revenue metrics, we need:

1. A parser that handles: `$150`, `150.00`, `$1,200`, `150`, ranges like `$100-$200` (take midpoint)
2. A new nullable `total_numeric` column (Decimal) populated post-extraction
3. Update the classification service to parse after AI extraction

### No shift/period tracking

There's no concept of a "shift" in the data model. Two options:

- **Option A (recommended):** Rolling window based on created_at timestamps. Configurable shift start time in settings. No DB changes needed.
- **Option B:** Explicit `Shift` model with start/end times. More precise but requires dispatcher to clock in/out or manual creation.

### Historical data coverage

For YTD and "not collected" analysis:

- Query `incoming_messages` for all dates in 2026
- Cross-reference with expected message days (exclude known off-days)
- Generate list of dates with zero incoming messages
- Compare against company phone_numbers to identify which companies went silent

---

## Database Changes Summary

| Change | Type | Migration |
|--------|------|-----------|
| `dispatch_jobs.total_numeric` DECIMAL nullable | New column | Alembic |
| `reports` table (id, type, date_range, payload JSONB, generated_at) | New table | Alembic |
| Index on `dispatch_jobs.created_at` | New index | Alembic |
| Index on `incoming_messages.created_at` | New index | Alembic |
| `company_metrics_cache` materialized view or table | Performance | Optional |

---

## Configuration Additions

New settings in `core/config.py`:

```python
SHIFT_DURATION_HOURS: int = 8
SHIFT_START_HOUR: int = 6  # 6 AM default
ANOMALY_SPIKE_THRESHOLD: float = 2.0  # standard deviations
REPORT_TIMEZONE: str = "America/Chicago"
DAILY_REPORT_ENABLED: bool = False
DAILY_REPORT_TIME: str = "23:00"
```

---

## File Map (New Files to Create)

```
backend/app/
  schemas/
    analytics.py          # Analytics response schemas
    shift.py              # Shift response schemas
  repositories/
    analytics.py          # Aggregation queries
    shift.py              # Shift queries
  services/
    analytics.py          # Analytics business logic
    shift.py              # Shift logic + alerts
  api/routes/v1/
    analytics.py          # Analytics endpoints
    shift.py              # Shift endpoints
  agents/tools/
    shift_tools.py        # AI agent shift tools
    company_tools.py      # AI agent company tools
    analytics_tools.py    # AI agent analytics tools

frontend/src/
  app/(dashboard)/
    analytics/
      page.tsx
      daily/page.tsx
      companies/page.tsx
      company/[id]/page.tsx
      ytd/page.tsx
      gaps/page.tsx
    shift/
      page.tsx
```
