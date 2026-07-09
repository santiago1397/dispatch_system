# Recent upgrades — job lifecycle (2026-07)

- **Operator reject**: "pass"/"have it"/"<zip> pass"/re-paste (WhatsApp + Quo) → job `rejected`, no alerts.
- **Tech accept/reject** (after dispatch): "ok"/"k" → `accepted`; "pass"/"no" → back to `pending` (re-dispatchable).
- **Tech updates**: `in_progress` / `appt_set` (+date/time) / `needs_follow_up` (+callback time) / `canceled` (+reason).
- **Alerts**: undispatched (5m), follow-up-due reminder, company-update-unsent (7m). **Company relay**: composes "job + update" for the operator to send.
- **Closing signal** (`services/closing_signal.py`): a tech's payment re-paste ("Paid $200 cash", "Close 240 cash", "4100$cc") in any tracked chat, WhatsApp **or** OpenPhone, marks the matched job `completed` and short-circuits classification (no spurious linked job). Regex gate (keyword + amount) + company-agnostic address+phone match; LLM-free.
- **Alert `closing_unfiled`** (15m): a job stuck in `completed` = tech reported payment but the operator hasn't filed the closing in the "Dispatch Closing" group yet. Self-resolves when the closing lands (`completed` → `closed`). No overlap with the 24h `closing_missing`.
- Migration `2026_07_06_tech_company_relays` (jobs cols + `company_updates`); run `python -m alembic upgrade head`. The closing-signal work needs **no** migration (reuses existing `lifecycle_status` / `alerts.kind` VARCHARs).
