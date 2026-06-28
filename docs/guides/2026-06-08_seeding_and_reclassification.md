# Seeding Companies & Reclassifying Messages

**Date:** 2026-06-08

How company patterns get into the database, and how to retroactively classify
messages that landed before the pipeline was wired up. Audience: the operator
running the system on a daily basis.

---

## TL;DR

```bash
# 1. Edit patterns in the notebook (one-time per change)
$EDITOR "C:\Users\santi\OneDrive\Documents\Python Scripts\dispatch_job_information\data_analytics_dispatch\dispatch_jobs_analytics_!.ipynb"

# 2. Regenerate companies.json from the notebook
cd "C:\Users\santi\OneDrive\Documents\Python Scripts\dispatch_job_information\data_analytics_dispatch"
python generate_companies_seed.py

# 3. Apply to the database (drops + reinserts all companies)
cd "C:\Users\santi\OneDrive\Documents\web projects\dispatch_chicago\distpatch_bot\backend"
uv run agents_bots cmd seed-companies --clear

# 4. Bulk-reclassify any incoming_messages that landed before the seed
#    (skip if you've just installed and have no messages yet)
uv run python -c "
import asyncio, logging
logging.disable(logging.CRITICAL)
from app.db.session import async_session_maker
from app.services.classification import JobClassificationService
from app.repositories import openphone_repo

async def main():
    async with async_session_maker() as db:
        msgs = await openphone_repo.list_incoming_messages(db, skip=0, limit=10000)
        print(f'Classifying {len(msgs)} messages...', flush=True)
        svc = JobClassificationService(db)
        results = {}
        for m in msgs:
            try:
                job = await svc.classify_message(m)
                results[job.classification_status] = results.get(job.classification_status, 0) + 1
            except Exception as e:
                await db.rollback()
                print(f'  ERR msg {m.id}: {type(e).__name__}: {str(e)[:80]}', flush=True)
                return
        await db.commit()
        print(f'Done. By status: {results}', flush=True)

asyncio.run(main())
"
```

The `/jobs` page should now have rows.

---

## The pipeline, in three steps

### Step 1 — Notebook is the source of truth

```
C:\Users\santi\OneDrive\Documents\Python Scripts\dispatch_job_information\
    data_analytics_dispatch\dispatch_jobs_analytics_!.ipynb
```

Two cells in this notebook drive everything:

| Cell | Variable | Purpose |
|------|----------|---------|
| 7 | `categories` (inside `identify()`) | All-of-group regex patterns, keyed by `COMPANY_N` variant label |
| 26 | `replacement_dict` | Maps each `COMPANY_N` to its canonical `COMPANY` name |

`categories` has ~80 variant labels; `replacement_dict` collapses them to
**~37 unique companies** (e.g. `SHAHAF_1`…`SHAHAF_7` all → `SHAHAF`).

A "group" in the categories is a list of regex patterns that must **all**
match the message for that company to be a candidate. E.g. SLK has:

```python
"SLK_3": [r"New\sjob\s#[A-Z0-9]{6}", "2037699944"],
```

…meaning "SLK" if the message has both `New job #ABC123` *and* the phone
`2037699944`. The notebook tries every variant in order; first one whose
all-must-match group fires wins.

### Step 2 — `generate_companies_seed.py` (notebook → JSON)

```
C:\Users\santi\OneDrive\Documents\Python Scripts\dispatch_job_information\
    data_analytics_dispatch\generate_companies_seed.py
```

This script:

1. Loads the notebook via JSON.
2. Parses `categories` and `replacement_dict` out of cells 7 and 26 using
   the AST (so the function-local `categories = {…}` inside `identify()`
   is picked up correctly).
3. Fills in any variant labels missing from the replacement dict with a
   default mapping (`MOBILE_1 → MOBILE`, `911_15 → 911`, etc.).
4. Applies a `NAME_ALIASES` table that renames the notebook's short names
   to the seed's longer names: `A1 → A1_LOCKSMITH`,
   `LOCKSMITH_DOWNTON → LOCKSMITH_DOWNTOWN`. Add future renames there.
5. Splits each variant's `patterns` list into:
   - **Phone numbers** — single patterns of `≥10` raw digits, no regex
     metachars (e.g. `9547371708`, `1-847-321-7619`, `(213) 668-5648`).
     These go into the company's `phone_numbers` array. The backend's
     primary classifier (`company_repo.get_by_phone_number`) runs first
     and bypasses regex entirely.
   - **Regex groups** — everything else, kept as `{"patterns": […]}` so
     the all-must-match semantics are preserved.
6. **Merges with the existing JSON** — any `phone_numbers` an operator
   added by hand are preserved. Any company in the existing JSON but
   missing from the notebook is kept (with a `NOTE:` warning) so the
   seed doesn't silently delete data.
7. Writes `distpatch_bot/backend/app/data/companies.json`.

Output is sorted by company name and stable — running the script twice
in a row with no notebook changes produces byte-identical output.

Useful flags:

```bash
python generate_companies_seed.py --print-only   # emit to stdout, don't write
python generate_companies_seed.py --notebook PATH --out PATH
```

### Step 3 — `agents_bots cmd seed-companies --clear` (JSON → DB)

```bash
cd "C:\Users\santi\OneDrive\Documents\web projects\dispatch_chicago\distpatch_bot\backend"
uv run agents_bots cmd seed-companies --clear
```

Loads `app/data/companies.json`, clears the `companies` table, and
inserts all rows in a single transaction. Without `--clear`, the command
no-ops if any companies already exist.

**Important fix in this version (2026-06-08):** the command previously
opened the JSON with the platform default encoding (cp1252 on Windows),
which crashed on the `©️` emoji in LOCKSMITH_PRO's pattern. The fix is
in `app/commands/seed_companies.py:34` — file is now opened with
`encoding="utf-8"`. If you're on a fresh checkout and the command dies
with a `UnicodeDecodeError` on `byte 0x8f`, that's the bug returning.
Re-apply the one-line fix.

---

## What `seed-companies` does *not* do

It loads companies. It does **not** retroactively classify
`incoming_messages` that arrived before the seed existed. The first 40
WhatsApp messages in the DB (June 8 2026, all at `10:56:13`) were
ingested before any companies were configured, so they sat as
unclassified. The bulk-reclassify snippet at the top of this doc is what
fixes that.

---

## Reclassification — two paths

### Path A: per-job (broken, see below)

```bash
curl -X POST http://localhost:8888/api/v1/dispatch/jobs/{id}/reclassify \
  -H "Authorization: Bearer $USER_JWT"
```

**Status: BROKEN** as of 2026-06-08. The endpoint delegates to
`DispatchJobService.reclassify()` in
`app/services/dispatch_job.py:58`, which:

1. Looks up the existing job.
2. Resets its fields to `None`.
3. Calls `JobClassificationService.classify_message(message)`.

Step 3 always tries to `INSERT INTO dispatch_jobs` (line 77 of
`classification.py`), and the table has `UNIQUE INDEX
dispatch_jobs_incoming_message_id_idx` on `incoming_message_id`. The
second reclassify on the same message therefore throws
`UniqueViolationError`. The first reclassify on a message that already
has a `dispatch_jobs` row fails the same way (the `reset` in step 2
doesn't delete the row, so step 3's `INSERT` still violates the unique
index).

**Workaround until fixed:** use the bulk snippet at the top of this
doc. It only inserts new `dispatch_jobs` rows and skips messages that
already have one — so a one-time `DELETE FROM dispatch_jobs WHERE
classification_status = 'failed' AND classification_error = 'No company
matched'` followed by the snippet is the cleanest path.

**Fix idea (not applied):** make `classify_message` accept an optional
`existing_job` parameter. When passed, skip `create_dispatch_job` and
operate on the existing row. ~10 lines of code.

### Path B: bulk over a date range

The snippet at the top of this doc is intentionally simple — no
parallelism, no batching. For 40 messages it completes in <1s. For
10,000+ messages you'll want to chunk (`skip=0, limit=500` in a loop
with `await db.commit()` per chunk) to avoid holding all results in
memory.

---

## The two pattern tiers, and why coverage is incomplete

The notebook's patterns target **structured ticket messages** — the
output of the dispatch companies' ticketing systems, e.g.:

```
New job #ABC123
Customer: John Smith
Address: 123 Main St, Chicago, IL 60601
Phone: 555-123-4567
Notes: front door lockout
```

The 40 real WhatsApp messages currently in the DB are **free-form chat
forwards** — the dispatch team copy-pasting from team chats, e.g.:

```
Ido Office
2037699944 #534
8732 Ridge St, River Grove, Illinois 60171
Regular key wth the fob
2021 Kia Soul
```

The same phone number appears in both formats, but the structural
cues (`New job #…`, `Notes:`, `Phone1:`, `Co:`, etc.) don't. Result:

| Format | Match rate (rough, on the 40 messages) |
|---|---|
| Structured ticket | High — most pattern groups fire |
| Free-form chat | Low — only 5/40 matched, those that had a discriminating phone in the body and the right company-specific phrasings |

**Three things would move the needle, in priority order:**

1. **Set `OPENAI_API_KEY` in `backend/.env`.** The AI tier of
   `JobClassificationService._classify_company_ai` will catch many of
   the 15 currently-`failed` messages. Without a key, you get the
   literal error string `Extraction failed: The api_key client option
   must be set…` on every job that gets past the regex step.
2. **Add a "tracked chat → company" rule** in
   `WhatsappService._classify_in_background` (or earlier in
   `ingest_batch`). The 28 `whatsapp_tracked_chats` have a
   `display_name` that often encodes the company ("Ido Office", "Nesar
   Jobs", "Sam sub closing"). A first-class mapping from chat title to
   company would bypass regex for any tracked chat and lift coverage
   significantly.
3. **Add free-form patterns** to the notebook, mirroring the structured
   ones. Example: for SLK, add a `SLK_ff: ["Ido Office", "2037699944"]`
   group that matches the body regardless of header. Order
   free-form groups *after* the structured ones in the JSON, so a
   structured match still wins when both fire.

---

## What depends on `OPENAI_API_KEY` being set

The classification pipeline has three stages, and the third is the
only one that needs OpenAI:

| Stage | What it does | Needs OpenAI? |
|---|---|---|
| 1. `identify_if_job` | Phone + address regex on the message body | No |
| 2. `identify` (company) | Phone lookup → regex groups → AI tier (catches unmatched messages) | **Yes** for the AI tier (3rd fallback) |
| 3. `_extract_fields` | Pull the 13 fields (address, customer, total, etc.) out of the body | **Yes** |

`OPENAI_API_KEY=` in `.env` means stage 3 always fails. The job lands
in `dispatch_jobs` with `classification_status='failed'` and
`classification_error='Extraction failed: … api_key client option …'`.
You'll see these on the `/jobs` page under the "Failed" status filter
and on each row's detail pane (red banner with the error text).

To unblock: set `OPENAI_API_KEY=…` in `backend/.env` and restart
uvicorn. Re-running the bulk-reclassify snippet will then pick up the
fields for the 5 currently-matched jobs.

---

## OpenRouter (free model) setup

The backend's LLM client is `langchain_openai.ChatOpenAI`, which
speaks the OpenAI HTTP protocol. Any provider that exposes the same
protocol works — just point `AI_BASE_URL` at it. The
`OPENAI_API_KEY` env var holds whichever provider's key, so there's
no rename to do.

### Get an OpenRouter key

1. Sign in at <https://openrouter.ai>.
2. Create a key at <https://openrouter.ai/settings/keys>. Free tier is
   enough for development; you get a small monthly credit and a
   permissive rate limit on `:free` models.
3. The key looks like `sk-or-v1-…`. Paste it into
   `OPENAI_API_KEY=` in `backend/.env`. **Don't commit it.** Treat it
   like the `sk_live_…` service-account key — same handling rules.

### Pick a model

`classification.py` uses `llm.with_structured_output(...)`, which
requires a model that supports **tool/function calling**. On OpenRouter:

| Model | Tools? | Notes |
|---|---|---|
| `qwen/qwen-2.5-72b-instruct:free` | ✅ | **Default in the seed `.env`.** Strong tool use, reliable extraction. |
| `meta-llama/llama-3.3-70b-instruct:free` | ✅ | Comparable to Qwen 2.5; choose whichever is more available. |
| `google/gemini-2.0-flash-exp:free` | ✅ | Fastest of the three; sometimes rate-limited. |
| `mistralai/mistral-nemo:free` | ✅ | Smaller, lower latency, slightly weaker on complex prompts. |
| Smaller 7B/8B `:free` chat models | ❌ | **Don't use these** — they fail on `with_structured_output` with a "model does not support tools" error. |

The list above is current as of 2026-06-08; OpenRouter's free catalog
rotates. Confirm tool support by filtering
<https://openrouter.ai/models?max_price=0> and checking the
"Supported parameters" column for `tools`.

### `.env` shape

```bash
# OpenRouter
OPENAI_API_KEY=sk-or-v1-…   # ← paste your key here
AI_MODEL=qwen/qwen-2.5-72b-instruct:free
AI_BASE_URL=https://openrouter.ai/api/v1

# Or OpenAI direct (uncomment + set the key):
# OPENAI_API_KEY=sk-…
# AI_MODEL=gpt-4o-mini
# AI_BASE_URL=https://api.openai.com/v1
```

Restart uvicorn after changing `.env` so the new settings are picked
up. There is no in-process hot-reload of env vars.

### Free-tier gotchas

- **20 requests/minute** typical rate limit on `:free` models. Plenty
  for the 5 currently-extraction-blocked jobs and the 15 AI-tier
  fallbacks; not enough for a real-time bulk batch larger than a few
  hundred. The code does not retry on 429.
- **`with_structured_output` may produce different schemas on different
  models.** If you see "validation error" in the logs after switching
  models, the prompt in `_classify_company_ai` or `_extract_fields` may
  need tightening for that model. Qwen 2.5 and Llama 3.3 both handle
  the current prompts cleanly.
- **Model catalogs change.** A model listed as `:free` today may move
  to paid tomorrow. If classification suddenly fails with HTTP 402
  (payment required) instead of 401 (auth), the model is no longer
  free — pick another.

---

## Files touched by this workflow

| File | Owner | When it changes |
|---|---|---|
| `dispatch_jobs_analytics_!.ipynb` | You (operator) | When a company changes its ticket format, or a new company appears |
| `generate_companies_seed.py` | Us | When we add a new heuristic for "what counts as a phone pattern" |
| `distpatch_bot/backend/app/data/companies.json` | Generated | Every time you re-run the generator |
| `distpatch_bot/backend/app/commands/seed_companies.py` | Us | Bug fixes (e.g. the 2026-06-08 cp1252 fix) |
| `distpatch_bot/backend/app/services/classification.py` | Us | When the reclassify unique-constraint bug is fixed |
| `distpatch_bot/backend/.env` | You | When you add `OPENAI_API_KEY` |
