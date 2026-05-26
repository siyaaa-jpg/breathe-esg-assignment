# Decisions

Every ambiguity in the brief we resolved, what we chose, why, and what we would
ask the PM if we could. Grouped by topic. The MODEL.md file carries the
data-model decisions; this is everything else.

---

## Stack and deployment

### One Render service serving Django + built React, not two services
Single Docker service: backend serves the React build via WhiteNoise; SPA
routes resolve through a Django catch-all that renders `index.html`. Avoids
CORS, lets us use Django session auth (same-origin cookies), one URL to share,
one deploy step. Cost: a slightly more complex build (Vite build → Django
collectstatic → gunicorn). Worth it for a 4-day prototype on Render's free
tier where every additional service is another sleeping container.

Would ask PM: do you anticipate the analyst dashboard ever being consumed by
a third-party tool (e.g., a Tableau plugin)? If yes, the answer changes —
separating the backend behind a JSON API on a dedicated domain becomes worth
the operational cost.

### SQLite for dev, Postgres for prod
SQLite is zero-install for the user (they're learning the stack). Postgres
on Render free tier for production. JSONField works on both via Django's
`models.JSONField`. GIN indexes on JSONB are Postgres-only and not added in
v1; the MODEL.md §10 indexes are all B-tree and portable.

Would ask PM: any need for Postgres-specific features in dev (LISTEN/NOTIFY,
full-text search, materialized views)? If yes, we'd require Postgres locally too.

### Python 3.14 dev, 3.13 in Docker
The user's local Python is 3.14 (very recent). Django 5.2 officially supports
through 3.13 — works in practice on 3.14 for our usage, but the production
container pins 3.13 to stay inside the supported window. Two-version dev/prod
is a real risk; we keep it small by avoiding 3.14-specific syntax.

### Auth: Django session auth (no JWT, no token auth)
Single origin, server-rendered login form. Simplest path. Reviewer logs in
once via Django's `/admin/login/`, cookie carries through. The frontend
detects 403s on GET and redirects to `/admin/login/?next=...`. No JWT refresh
logic, no token rotation, no localStorage XSS surface. If we needed mobile or
third-party clients, we'd add token auth as a second authentication class on
DRF — schema unchanged.

### Docker deploy with multi-stage build (Node + Python)
Render's native Python runtime doesn't include Node by default. Docker is
the most reliable path for combined frontend + backend. Two stages:
node:20-alpine builds the React app, python:3.13-slim runs Django. The
backend stage copies only the built `dist/` from the frontend stage — keeps
the runtime image small. `render.yaml` declares the service + the managed
Postgres in one file; Render reads it on deploy.

---

## Data model

(See MODEL.md for the full model and field-level rationale. This section
captures meta-decisions that don't fit in MODEL.md.)

### Twelve tables in v1, no inheritance, one shared TenantScopedManager
Considered an abstract base `TenantScopedModel` — would have been clever,
would have been one more thing to explain. Skipped. Each model has an
explicit `organization` FK and uses the same `TenantScopedManager` (which
adds a `.for_org(org)` shortcut). Three lines repeated across models, but
obvious to a reader.

### UUIDs for primary keys (except ActivityType — int)
UUIDs everywhere we'll expose IDs in URLs or share them across systems.
ActivityType is a small (~11-row) lookup table managed by us; integer PKs
keep fixtures and the demo data human-readable.

### Decimal precision: 18,4 for quantities, 12,6 for factors
Quantities go large (a refinery's annual fuel is millions of liters).
Factors go small (a kWh of grid electricity ≈ 0.4 kg CO2e; biogenic methane
factors can be 0.000028 per kg). Both pick conservative ranges; we'll narrow
later if real data shows narrower distributions.

### `EmissionRecord.activity_type` is nullable
When SAP gives us a material code with no MaterialCodeMapping, we still want
the row to appear in the analyst's review queue so they can classify it.
Hence nullable — the corresponding `quantity`, `co2e_kg`, and
`emission_factor` are also null until the analyst resolves the mapping. The
flag `material_unknown` tells them why. See MODEL.md §5.

---

## Ingestion

### One shared `BaseIngester`, three thin per-source subclasses
The universal logic (file dedup via SHA256, IngestionBatch creation,
facility resolution, activity-type lookup, unit normalization via
`derive_co2e`, factor lookup, EmissionRecord creation, counter updates) is
identical across all three sources. Putting it three times would drift; we
abstracted into `BaseIngester` and each subclass only implements `parse()`.
Cost: a reader has to understand BaseIngester before reading a per-source
ingester. Justified by the duplication savings.

### `derive_co2e` shared between ingestion and the analyst edit path
When an analyst edits a record (e.g., fixes the unit), the system has to
re-normalize, re-lookup the factor, and re-compute co2e — exactly the same
work the ingester did originally. We extracted this pipeline into
`apps/emissions/services.py::derive_co2e`. Both paths call it. Single source
of truth for "given inputs, derive outputs and flags."

### Flag policy: soft flag whenever the row is structurally complete
A flag means "we created the record, but the analyst should look." A hard
error means "we couldn't create the record at all." Hard errors land in
`IngestionBatch.parse_errors` (the analyst sees a count but can't act on
individual rows); flags land on the record itself in the review queue.
Default to flag — analyst-actionable is more useful than analyst-invisible.

### Lazy-creating unmapped PlantCodeMapping / MaterialCodeMapping rows
When SAP ingestion sees `DE99` (no mapping), we create a stub
`PlantCodeMapping(sap_plant_code="DE99", facility=None)` so admin sees it
in the admin's list view with a "(unmapped)" label. Without this, unmapped
codes are scattered — admin would have to query the upload's records to
find what to map. Lazy creation centralizes the work.

### File-level dedup via SHA256
Re-uploading the exact same bytes (same org) returns 409. The natural-key
dedup (source_row_identifier) is per-row and runs at ingest time, producing
a `duplicate_candidate` flag rather than blocking. Two layers, two purposes:
hash prevents accidental re-upload, row-level dedup catches re-issued data.

Would ask PM: when an analyst legitimately re-uploads a corrected file
(e.g., utility issued a revised bill), what's the workflow? Currently the
hash check rejects it; the analyst would need to delete the original batch
first (or upload with an edited byte to bypass — bad UX). A real version
needs a "supersede" flow.

### Synchronous ingestion, no Celery
All parsing happens in-request. Works fine for files up to ~1000 rows.
Above that, Render's 30s request timeout kicks in. Adding Celery + Redis
would mean two more Render services on the free tier, plus a frontend
polling state machine. Out of scope for the prototype. Called out in
TRADEOFFS.md.

---

## Analyst UX

### Reuse Django admin's login UI, not custom
The brief weights "analyst UX" at 10%, and the analyst's first interaction
is logging in. We don't build a custom login UI — the React app detects a
403 and redirects to `/admin/login/?next=...`. After login, the admin
redirects back to the SPA root. This is uglier than a polished custom login
but saves a day; the analyst sees it once.

Would ask PM: does the analyst need to look "client-facing" (logo, brand,
custom domain), or is this an internal tool? If internal, the admin-login
shortcut is fine. If client-facing, we'd build a proper login screen and
move auth out of `/admin/`.

### One review queue, filterable, not separate "pending" / "flagged" / "approved" tabs
Status is a filter, not a tab. Reasons:
1. The analyst's mental model is "show me everything from this upload" or
   "show me everything that needs attention," not "switch to the flagged
   tab." A single filterable view supports both.
2. Tabs hide cross-status filters (e.g., "all flagged records from Frankfurt
   for Q3"). A single filterable view supports composition.
3. One implementation, half the code.

### "Approve / Reject / Lock" as three buttons on the detail view, not bulk actions
Bulk actions (approve N records at once) are useful for real analyst
workflow but not needed to demonstrate the lifecycle. We have a single
record view with three action buttons; analyst clicks through one row at a
time. Called out as a deliberate cut.

### Edit form with mandatory reason
Every edit requires a free-text `reason`. Backend enforces (returns 400 if
missing). The reason lands in EmissionRecordEdit and shows up in the audit
trail at the bottom of the detail view. This is the cheapest mechanism to
make the audit trail informative — reasons turn `"100" -> "200"` into
`"100" -> "200": "Source had stale meter reading; correcting from invoice"`.

### Side-by-side original vs normalized on detail view
The Display answers two questions the analyst always has: "what did the
source say?" and "what did we compute?" Putting them next to each other
makes mismatches obvious (e.g., "MWh in source, kWh in normalized — we
multiplied by 1000"). Without this, the analyst is mentally tracking which
column means which.

### Raw source payload exposed as collapsible JSON
The brief calls out "source-of-truth tracking." The MODEL.md JSONField
preserves the raw row; the detail view shows it in a `<pre>` block. An
analyst (or auditor) can see exactly what came in, byte for byte.

---

## Frontend

### No state-management library, no React Query, no Tailwind
Plain `useState`/`useEffect` + `fetch` + one CSS file. Tradeoffs:
- **No TanStack Query** → we write refetch boilerplate manually after
  mutations. Cost: ~50 lines. Benefit: zero magic, easier to defend.
- **No component library** → we write 6-8 small components (Pill,
  FlagChips). Cost: ~200 lines. Benefit: every CSS class is in our
  codebase, no fighting with library defaults.
- **No Tailwind** → one CSS file, ~250 lines. Cost: not utility-first.
  Benefit: easier for a Django dev to read end-to-end.

The user is "learning both Django and React"; every dependency is one more
thing to defend in an interview. Keeping the stack small was an explicit
choice driven by that.

---

## What we'd ask the PM (the load-bearing questions)

Pulled from various sections above for ease of scanning:

1. **How do you actually close periods?** Per-row (analyst-driven), per-batch,
   or per-fiscal-quarter? We assumed per-row but per-period is more realistic
   and would change the data model (need `ReportingPeriod` table).
2. **Multi-org users?** A Breathe consultant working across three client
   orgs — yes or no? Affects User model (currently one-to-one with Org).
3. **Restatement workflow when emission factors update mid-year** — does the
   system auto-restate, prompt the analyst, or stay frozen? We chose frozen
   (factor is FK'd at compute time, never auto-updated).
4. **Hard audit standard requirements (ISO 14064, CDP, SBTi verification)?**
   Some standards have specific provenance/restatement/sign-off requirements
   we'd encode differently.
5. **Bulk file re-upload flow** when a source issues a corrected file
   (revised utility bill, restated SAP posting). Current hash-based dedup
   blocks it; a real flow needs supersede semantics.
6. **Client-facing or internal tool?** Determines whether we replace
   `/admin/login/` with a custom login UI.
