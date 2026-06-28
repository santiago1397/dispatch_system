# Companies Seed Reference

Snapshot of the active company set loaded by `uv run agents_bots cmd seed-companies --clear` on **2026-06-08**. Source file: `backend/app/data/companies.json`. This is the output of the last `generate_companies_seed.py` run, parsed from cells 7 (`categories`) and 26 (`replacement_dict`) of the dispatch notebook.

> **Trim 2026-06-08:** 23 companies were removed by hand from `companies.json` only (911, 911_JETS, AGN, BEN, BERKO, CESAR, DENNIS, GLOBAL, HEN, IAG, LIOR, LOCKSMITH_DOWNTOWN, LOCKSMITH_EXPRESS, LOCKSMITH_PRO, LOCKX, LOCK_N_SMITH, MOBILE, MOR, OFEK, SEND_A_LEAD, SHALEV, YFRAH, YMS). The notebook was **not** edited, so a future `python generate_companies_seed.py` run from the notebook will resurrect them. To make the trim permanent, edit cells 7 and 26 of the dispatch notebook to drop the corresponding variant labels.

**14 companies total.** 11 use only regex, 1 uses only phone-number lookup (`ALL_FIX`), 2 use **both** (SHAHAF, SLK). Across all 3 phone-populated companies, 4 distinct phone numbers are registered.

## How patterns are evaluated

`backend/app/services/classification.py:183-200` — `_classify_company_regex` iterates active companies and returns the **first** whose pattern group fully matches. Within a group, **all** patterns must match (`re.IGNORECASE | re.MULTILINE`). Groups themselves are OR'd. So a company is a match if *any one* of its groups has *every pattern inside* hit.

Phone-number matching is run first (`classification.py:90`) and short-circuits regex/AI entirely. Companies with only `phone_numbers` skip regex entirely.

## At-a-glance

| # | Name | Display | Regex groups | Phone-only | Notes |
|---|------|---------|---:|---|---|
| 1 | `A1_LOCKSMITH` | A1 Locksmith | 3 | — | "A1 Locksmith" + structured ticket header (`JOB ID:`, `Source: Agency`) + fuzzy line-match. |
| 2 | `ABC` | ABC Locksmith | 2 | — | "ABC Locksmith" or "Eric's Locksmith". |
| 3 | `ALL_FIX` | All Fix | 0 | ✅ | **Phone-only: 8777711892.** No regex. |
| 4 | `AMS` | Always 24/7 | 5 | — | "Co: Always 24/7" + fuzzy. |
| 5 | `ASAP_LOCKSMITH` | A.S.A.P Services | 3 | — | "A.S.A.P Services" + `Job ID: #…` ticket pattern. |
| 6 | `GARAGE_LEAD` | Garage Lead | 1 | — | Structured: a capitalized word line + `(10digits)` + `addr:` + `Service` + `1-2digit-1-2digit` time range. |
| 7 | `JOSEPH_LOCKSMITH` | Joseph Locksmith | 3 | — | "Joseph locksmith" + misspelling "Locksmirh" + bare "Joseph" line. |
| 8 | `ONE_AND_ONLY` | 1 And Only | 4 | — | "1 And Only" + `Job ID:` + `Type:`; also fuzzy "1nOnly" / "1 and only" / "One N Only". |
| 9 | `ON_CALL` | On Call Locksmith | 3 | — | Case-variants. |
| 10 | `PROFESSIONAL_LOCKSMITH` | Professional Locksmith | 5 | — | `J#\d{10}` / `E#\d{10}` ticket ID + `\| MM/DD/YYYY` date separator. |
| 11 | `SADAN` | Sadan | 8 | — | `Job: XXXXX` + `Phone1:` + `Address:`; also catches "Crystal Locksmith" + several "Source: …" sub-brokers (Lucky, Bates, Around The Clock, And Security Group) and Mega/Chilocksmith. |
| 12 | `SHAHAF` | Shahaf Locksmith | 5 | ✅ | `New job #XXXXXX` + `Locksmith 24/7` + (10digits #N). **Phone-first: 6054507491, 6506754074.** |
| 13 | `SLK` | SLK | 4 | ✅ | `New job #XXXXXX` + (10digits #N) + `Notes:`. **Phone-first: 2037699944.** |
| 14 | `USAFE` | Usafe Locksmith | 5 | — | `getjobox.com` + `PDL: XXXXX` + `Svc + labor`; also "Ref: Us Garage Door". |

51 regex groups, 4 phone numbers. After the trim, `SADAN` has the largest single-company regex set (8), down from the pre-trim tie between 911 and IAG (16 each).

## Companies with phone numbers

3 companies, 4 numbers. The "phone-only" column in the at-a-glance table marks which path short-circuits — but the **phone match always runs first** (`classification.py:90`), so for SHAHAF and SLK a phone hit will win over a regex hit. For `ALL_FIX` there is no regex to fall back to.

Note: the `_is_job_message` gate (`phone AND address`) still applies even when the phone already picked the company. Phone lookup decides the **company**; the gate decides whether the message is a **job** at all.

| Name | Phone numbers | Path |
|------|---|---|
| `ALL_FIX` | 8777711892 | phone only (no regex) |
| `SHAHAF` | 6054507491, 6506754074 | phone-first, regex fallback |
| `SLK` | 2037699944 | phone-first, regex fallback |

## Pattern shape taxonomy

Three shapes recur across the 13 regex-using companies:

1. **Literal name match** — `"ABC Locksmith"`, `"1 and only"`, `"Mega Locksmith"`. Cheapest, most brittle.
2. **Structured header** — `JOB ID:` + `Source: Agency` (A1_LOCKSMITH), `Job ID: #…` (ASAP_LOCKSMITH), `Co: Always 24/7` + `PDL:` + `Occu:` (AMS), `J#\d{10}` + `\| MM/DD/YYYY` (PROFESSIONAL_LOCKSMITH), `getjobox.com` + `PDL:` + `Svc + labor` (USAFE). These are the highest-precision patterns.
3. **Fuzzy line match** — `(?:.*\n){0,1}.*XYZ.*(?:\n|)$`. A 2-line window where the line before (or the line itself) contains the brand name. Used as a fallback when structured headers are absent. Loose — will over-match if the chat has stray mentions.

Tier 3 is why `SADAN` and `AMS` still match chats that don't carry their full template — both lean on fuzzy line matches as a fallback when the structured header is absent.

## How to extend

Don't edit `companies.json` directly — the next `generate_companies_seed.py` run will clobber everything **except** `phone_numbers` (the script preserves those). Add the new variant in the dispatch notebook:

- **New regex group for an existing company** → add to the `categories[name]` dict in cell 7.
- **New phone number for an existing company** → add to the `phone_numbers` field in `companies.json` (hand-edit, the script preserves it).
- **New company** → add a new key to `categories` in cell 7, and add a canonicalization entry in `replacement_dict` in cell 26.

Then re-run:

```bash
cd "../Python Scripts/dispatch_job_information/data_analytics_dispatch"
python generate_companies_seed.py
cd "../../web projects/dispatch_chicago/distpatch_bot/backend"
uv run agents_bots cmd seed-companies --clear
```

Full pipeline walkthrough: [`2026-06-08_seeding_and_reclassification.md`](2026-06-08_seeding_and_reclassification.md).
