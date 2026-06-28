# WhatsApp Web Ingestion — Chrome Extension Module

**Date:** 2026-06-04

> **Updated 2026-06-08:** Added the `OPENAI_API_KEY` requirement
> (without it, every job that gets past the regex stage fails with
> "api_key client option must be set"). Reclassify endpoint is
> currently broken — see the "Seeding companies & reclassifying" guide
> for the bulk-reclassify workaround. The seed command is now
> `agents_bots cmd seed-companies` (not `agents_bots seed-companies`).

A second ingestion source for the dispatch system, alongside the existing
OpenPhone / Quo webhook. A dispatcher who coordinates with drivers and
contractors over WhatsApp groups needs those messages searchable next to the
OpenPhone history.

The implementation is a new module inside `distpatch_bot` (new tables, new
routes, new service-account auth) plus a Chrome MV3 extension in
`dispatch_extension/` that scrapes WhatsApp Web and posts to the new
endpoints.

**See also:**
- [Seeding companies & reclassifying messages](2026-06-08_seeding_and_reclassification.md) — the notebook → `companies.json` → DB workflow, and how to retroactively classify messages.

---

## Why a Chrome extension (not server-side Playwright)

`distpatch_bot` already has Playwright wired in (`app/browser/manager.py`,
`BROWSER_ENABLED` env), so server-side scraping would be technically
trivial. The decision against it is **trust boundary**: the extension reads
the WhatsApp Web tab inside the user's own browser, while server-side
Playwright would hold the full session cookie and see *all* the user's
WhatsApp chats. The scraper only persists whitelisted ones, but the browser
session exposes everything.

The extension is the right call when the source WhatsApp account is the
user's personal account. Server-side Playwright is only safe for a
dedicated dispatch phone.

---

## Module layout (backend)

| File | Purpose |
|---|---|
| `app/db/models/whatsapp.py` | `WhatsappTrackedChat`, `WhatsappMessage` models |
| `app/db/models/user.py` | +5 service-account columns on `User` |
| `app/schemas/whatsapp.py` | Pydantic v2 mirrors for both models + batch ingest + service-token |
| `app/repositories/whatsapp.py` | `upsert_message`, `upsert_chat`, `list_messages`, etc. |
| `app/services/whatsapp.py` | `WhatsappService` — class-based, mirrors `OpenPhoneService` |
| `app/api/routes/v1/whatsapp.py` | 8 endpoints under `/api/v1/whatsapp/` |
| `app/api/deps.py` | `get_service_account`, `CurrentServiceAccount` |
| `app/core/security.py` | `extra_claims` on JWT, `hash_api_key` / `verify_api_key` |
| `app/commands/whatsapp.py` (via `cli/commands.py`) | `user create-service-account` |
| `alembic/versions/...` | baseline migration adding the new tables |

The `whatsapp_messages` and `whatsapp_tracked_chats` tables are independent
of `dispatch_jobs` in v1. WhatsApp group chats have a different shape from
SMS — one job spread across many messages, lots of non-job chatter. A
multi-message classifier that creates `DispatchJob` rows from WhatsApp
threads is a v2 feature.

---

## Auth model

```
┌────────────────┐  sk_live_xxxx   ┌────────────────┐
│  Admin (CLI)   │ ───────────────▶│  POST /auth/   │
│  create-       │                 │  service-token │
│  service-      │                 └────────┬───────┘
│  account       │                          │
└────────────────┘                          ▼
                                  ┌─────────────────────┐
                                  │  {access, refresh}  │
                                  │  JWT pair           │
                                  │  svc: true claim    │
                                  └────────┬────────────┘
                                           │
                  ┌────────────────────────┴──────────────┐
                  ▼                                       ▼
        POST /messages/batch                  GET/PATCH /tracked-chats
        (with Authorization: Bearer)          (admin user JWT, not svc)
```

**API key → JWT** flow:
1. Admin runs `agents_bots user create-service-account --name "Dispatcher iPhone"`.
2. CLI generates `sk_live_<32 hex>`, stores bcrypt hash + 12-char prefix on
   the `User` row, prints the plaintext key **once**.
3. Extension popup collects the key, calls `POST /api/v1/whatsapp/auth/service-token`
   with header `X-Service-Api-Key: sk_live_…`.
4. Backend looks up the user by the 12-char prefix (fast indexed lookup),
   bcrypt-verifies the full key, returns a JWT pair with `svc: true` claim.
5. All subsequent calls use `Authorization: Bearer <access_token>`. The
   access token is short-lived; the extension calls
   `POST /api/v1/whatsapp/auth/refresh` with `X-Refresh-Token` to rotate.

Service JWTs are distinguished from user JWTs by the `svc: true` claim
*and* by checking `user.is_service_account is True` (defense in depth — a
user JWT can never be used to write batches even if the claim is forged).

---

## API reference

### Service-Account Auth

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/api/v1/whatsapp/auth/service-token` | `X-Service-Api-Key` header | Exchange API key for JWT pair |
| POST | `/api/v1/whatsapp/auth/refresh` | `X-Refresh-Token` header | Rotate refresh token, preserve `svc: true` |

### Message Ingestion

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/api/v1/whatsapp/messages/batch` | `CurrentServiceAccount` | Idempotent batch upsert. Returns `{inserted, updated, skipped, errors}` |
| GET  | `/api/v1/whatsapp/messages` | `CurrentUser` | Browse persisted messages. Query: `chat_jid`, `since`, `until`, `sender`, `contains`, `skip`, `limit` |

### Tracked Chats (whitelist)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET    | `/api/v1/whatsapp/tracked-chats` | `CurrentUser` or `CurrentServiceAccount` | List active chats |
| POST   | `/api/v1/whatsapp/tracked-chats` | `CurrentUser` | Add a chat (idempotent upsert) |
| PATCH  | `/api/v1/whatsapp/tracked-chats/{chat_jid}` | `CurrentUser` | Update display name or `is_active` |
| DELETE | `/api/v1/whatsapp/tracked-chats/{chat_jid}` | `CurrentUser` | Soft-delete (`is_active=false`) |

### `POST /messages/batch` request body

```json
{
  "messages": [
    {
      "wa_message_id": "3EB0B4B5A06",
      "chat_jid": "120363024336125901@g.us",
      "sender_name": "Maria Lopez",
      "is_from_me": false,
      "body": "Need a tow at 1240 N Paulina, ASAP",
      "timestamp": "2026-06-04T15:42:18Z",
      "media_type": null,
      "reactions": []
    }
  ]
}
```

Response:
```json
{
  "inserted": 1,
  "updated": 0,
  "skipped": 0,
  "errors": []
}
```

### Idempotency and the timestamp guard

The DB has `UNIQUE(chat_jid, wa_message_id)`. The repository uses
PostgreSQL `INSERT ... ON CONFLICT DO UPDATE` with a timestamp guard:

```sql
INSERT INTO whatsapp_messages (...) VALUES (...)
ON CONFLICT (chat_jid, wa_message_id) DO UPDATE
SET body = EXCLUDED.body, edited_at = EXCLUDED.edited_at, ...
WHERE EXCLUDED.timestamp >= whatsapp_messages.timestamp;
```

The `WHERE` clause means an older message never overwrites a newer one. If
a `wa_message_id` is reused after a delete-and-resend, the new row
(assumed to be the edit) wins.

---

## Database migration

> **Updated 2026-06-05:** the original version of this section told you
> to autogenerate a migration "adding the whatsapp tables". Those tables
> are **already in the current schema** (created via
> `Base.metadata.create_all` during first-run bootstrap, since
> `alembic/versions/` was empty). You only need a migration when you
> *change* a model after the schema is built.

When you do change a model, the standard flow is:

```bash
cd distpatch_bot/backend
uv run alembic revision --autogenerate -m "describe the change"
uv run alembic upgrade head
```

**Important:** the autogenerated `incoming_messages` table will **not**
include the existing FK from `dispatch_jobs.incoming_message_id` to
`incoming_messages.id` because that relationship is one-way in the model
(`DispatchJob` has the FK column, `IncomingMessage` has the `relationship`).
Manually add the FK constraint to the migration after autogenerate.
Verify the diff against `app/db/models/dispatch_job.py` to confirm both
FKs (incoming_message_id → incoming_messages, company_id → companies)
are present.

The very first autogenerate is special: there are no prior revisions, so
Alembic will see the entire current schema as a brand-new baseline. Run
`alembic stamp head` after creating it, or diff it against
`Base.metadata` to confirm there are no real changes.

---

## Extension (frontend)

See `dispatch_extension/README.md` for end-user install and usage.

Key files:

- `dispatch_extension/manifest.json` — MV3, permissions `storage` + `alarms`,
  host permissions for `web.whatsapp.com` and the backend URL.
- `dispatch_extension/content/scraper.js` — runs in the WA Web page,
  MutationObserver + jittered discovery flush, 5s heartbeat, and the
  `SELECTORS` object + JID extractor inlined at the top. When the
  scraper silently stops working, the `SELECTORS` block is the first
  place to patch.
- `dispatch_extension/background/service-worker.js` — orchestrator. Routes
  `BATCH` messages to the buffer, ticks a 30s `chrome.alarms` flush,
  handles 401-retry-once with refresh.
- `dispatch_extension/lib/buffer.js` — in-memory + `chrome.storage.local`
  write-through buffer. 5MB cap, drops on overflow.
- `dispatch_extension/lib/logger.js` — append-only event log (last 500
  entries, persisted to `chrome.storage.local`). Powers the popup's
  **Activity log** panel — first place to look when something looks
  broken in the extension but you don't want to open DevTools.
- `dispatch_extension/lib/api.js` — fetch wrapper, JWT refresh, 401 retry.
- `dispatch_extension/popup/` — two views: connect form + tracked/discovered
  chat list.

### Anti-ban constraints baked in

- **Read-only DOM** — the content script never writes to the page.
- **Jittered timing** — 150–400 ms debounce on mutations, 4–7 s discovery
  flush. No fixed-interval pattern.
- **No automation** — the extension never clicks, scrolls, or types.
  You open the chats manually; the extension just records what is already
  on screen.
- **Network from the SW only** — all backend traffic is from the
  extension's own context, using the extension's permissions.

### Selector maintenance

`content/scraper.js` calls `runDomHealthCheck()` on boot, which logs
selector match counts to the console:

```
[whatsapp-scraper] DOM health check: { "MESSAGE_ROW": 17, "ACTIVE_CHAT_TITLE": 1, ... }
```

If a critical selector returns zero on a fresh WA Web page, the scraper
is broken and the only fix is in `content/selectors.js`.

---

## Setup (end-to-end)

> **Updated 2026-06-05 after the project's first end-to-end run.** The
> earlier version of this section assumed an `.env.example` and a populated
> `alembic/versions/` directory. Neither exists. Use this version.

1. **Backend**
   ```bash
   cd distpatch_bot/backend

   # Create .env (no template is checked in). Minimal local config:
   cat > .env <<'EOF'
   ENVIRONMENT=local
   DEBUG=true
   POSTGRES_HOST=localhost
   POSTGRES_PORT=5432
   POSTGRES_USER=postgres
   POSTGRES_PASSWORD=postgres
   POSTGRES_DB=dispatch_test
   SECRET_KEY=local-dev-secret-do-not-use-in-production-7f3a9b2c1d4e5f6a

   # Optional — leave blank to disable the respective integration.
   # WITHOUT OPENAI_API_KEY, every job that gets past the regex stage
   # fails with "api_key client option must be set". Set this for real
   # classification, including the 13-field extraction.
   OPENAI_API_KEY=
   EOF

   uv sync
   # NOTE: no alembic migrations exist. If the dispatch_test DB is empty,
   # bootstrap the schema with create_all. The CLI does NOT auto-create
   # the database — you must CREATE DATABASE first (use any name you like):
   psql -U postgres -c 'CREATE DATABASE dispatch_test;'   # or via asyncpg
   uv run --no-project python -c "
   import asyncio, asyncpg
   from sqlalchemy.ext.asyncio import create_async_engine
   from app.db.base import Base
   import app.db.models  # noqa
   async def boot():
       e = create_async_engine('postgresql+asyncpg://postgres:postgres@localhost:5432/dispatch_test')
       async with e.begin() as c: await c.run_sync(Base.metadata.create_all)
       await e.dispose()
   asyncio.run(boot())
   "

   uv run uvicorn app.main:app --reload --port 8888
   ```

2. **Pre-flight check** — if `uv run uvicorn` fails with
   `TypeError: Cannot create a consistent method resolution order (MRO)
   for bases str, StrEnum`, the `fastapi-fullstack` template bug is
   back. Fix:
   - `app/db/models/company.py` → `class PatternType(StrEnum):`
   - `app/db/models/dispatch_job.py` → `class ClassificationStatus(StrEnum):`
   (drop the redundant `str,` base — `StrEnum` already inherits from `str`).

3. **Seed companies** — the `companies` table starts empty. Without
   companies, every job lands in `dispatch_jobs` with status `failed`
   and error `No company matched`. The pipeline is described in
   [Seeding companies & reclassifying messages](2026-06-08_seeding_and_reclassification.md);
   the short version:
   ```bash
   # Generate companies.json from the dispatch notebook (operator-side)
   cd "C:\Users\santi\OneDrive\Documents\Python Scripts\dispatch_job_information\data_analytics_dispatch"
   python generate_companies_seed.py

   # Apply to the database
   cd "C:\Users\santi\OneDrive\Documents\web projects\dispatch_chicago\distpatch_bot\backend"
   uv run agents_bots cmd seed-companies --clear
   ```
   Then re-classify any `incoming_messages` that landed before the
   seed (see the linked guide for the bulk snippet — the per-job
   reclassify endpoint is broken as of 2026-06-08).

4. **Service account**
   ```bash
   uv run agents_bots user create-service-account --name "Dispatcher Browser"
   ```
   Copy the printed `sk_live_…` key. **The key is shown exactly once.**
   Don't paste it into chat logs or commit it — if leaked, delete the
   user row and create a new one.

5. **Extension**
   - Open `chrome://extensions` → toggle **Developer mode** ON.
   - **Click "Cargar descomprimida" (Load unpacked) — NOT "Empaquetar
     extensión" (Pack extension).** The Pack button creates a `.crx` +
     `.pem` and does NOT install. If you click it, you get a "could not
     package / error generating private key" message and no card
     appears, and you'll waste 20 minutes wondering why "Load unpacked"
     didn't work.
   - In the file picker, select the `dispatch_extension/` directory.
   - Click the green D icon → paste the key, click **Connect**. The
     status pill should flip to "Connected".

6. **WhatsApp Web**
   - Open `https://web.whatsapp.com`, complete QR pairing.
   - Open the dispatch groups you want to track. In the popup, click
     **Track** on each one.

7. **Verify**
   ```sql
   SELECT count(*) FROM whatsapp_messages;
   -- Should grow each time a tracked chat is opened.
   ```

---

## Open risks

1. **WhatsApp Web selector drift** — `data-id` format and `[data-testid=…]`
   values change. Centralize in `content/selectors.js`. A failure mode is
   "scraper stops working," not "account banned" — a different and
   recoverable problem.

2. **`wa_message_id` reuse after delete-and-resend** — the
   `(chat_jid, wa_message_id)` dedup treats it as the same row.
   Mitigation: timestamp guard. Tested.

3. **Backend unreachable for hours** — buffer caps at 5 MB (~2,500
   messages). Beyond that, new messages are dropped (popup banner
   surfaces this). For 24/7 unattended use, v2 should add a server-side
   ingestion worker with proper queueing.

4. **JWT refresh fails while user is offline** — refresh token expires
   after 7 days. After that, the user re-pastes the API key. Documented
   in the popup's logged-out state.

5. **Two extension instances on two machines with the same API key** —
   last-write-wins on `edited_at`. Expected. Documented.

6. **Pre-existing MRO issue in `app/db/models/company.py` and
   `app/db/models/dispatch_job.py`** — `class Foo(str, StrEnum)` is
   incompatible with Python 3.11+ (where `StrEnum` already inherits
   from `str`). Was fixed in 2026-06-05 but is the most common reason a
   fresh checkout fails on first `uv run`. The fix is to drop the
   redundant `str,` parent from the class declaration. See the
   "Pre-flight check" in the Setup section above.
