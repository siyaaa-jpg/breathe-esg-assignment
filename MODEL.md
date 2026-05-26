# Data model

## What this models

This is the model behind a prototype that ingests emissions activity data from three
source systems (SAP fuel/procurement, utility electricity, corporate travel), normalizes
it into a unified shape, and lets a human analyst review, edit, approve, and lock rows
before they go to auditors.

It is **not** a full carbon accounting system. It does not produce a CDP report, it does
not run scenario analysis, and it does not compute Scope 3 spend-based estimations from
procurement spend. It models the part of the pipeline where messy source data becomes
trusted audit-grade rows. Everything downstream (reports, dashboards, restatements
across years) would build on top of this.

## The central insight

Three source systems, three column shapes, three categories of emission factor — but
the analyst's job is the same regardless of source: look at a row, decide whether to
trust it, edit if needed, approve, lock. So the analyst-facing table is one shape:
**`EmissionRecord`**. Source-specific shape lives in the ingestion layer, not in the
schema.

A naive design would have `FuelRecord`, `ElectricityRecord`, `TravelRecord` as three
separate tables. That triples the audit trail code, the review queue code, the approval
state machine, and the read-side dashboard. The unified table costs us a slightly less
type-safe schema (the `quantity` field has different meaning per `activity_type`), but
that's a tradeoff worth making for a system whose value is in the workflow, not the
storage layout.

## Entity overview

```
┌─────────────────┐
│ Organization    │  tenant boundary
└─────┬───────────┘
      │
      ├──< User                  (role: analyst | admin)
      │
      ├──< Facility              (plant / office / fleet — the "where" of an activity)
      │
      ├──< PlantCodeMapping      (org-scoped: SAP plant code → Facility)
      │
      └──< IngestionBatch        (one upload event)
              │
              └──< EmissionRecord ◄─── the heart of the model
                        │
                        ├── ActivityType       (lookup: scope, canonical unit)
                        ├── EmissionFactor     (versioned by effective_date)
                        ├── Facility           (where it happened)
                        ├── source_payload     (JSONB; original raw row, untouched)
                        └──< EmissionRecordEdit  (append-only audit log)
```

Eleven tables total in v1. Listed in dependency order below.

---

## 1. Tenancy

**Choice**: row-level `organization_id` on every tenant-scoped table, enforced by a
default manager that filters by current org.

**Why this and not schema-per-tenant**: schema-per-tenant gives real database-level
isolation but doubles migration cost, complicates the Render deploy (manual schema
creation per signup), and makes cross-tenant tooling (which we'll want for an analyst
who works across two client orgs, or for support/debugging) painful. The threat model
for a demo isn't "what if a tenant SQL-injects another tenant" — it's "what if an
analyst's query accidentally returns another tenant's rows." Row-level scoping with a
default manager and FK-based filters handles that. If Breathe ESG ever sells into a
client with regulatory isolation requirements, the migration to schema-per-tenant is
real work but it's a known pattern (django-tenants); we'd cross that bridge when it
appears.

**`Organization`**
| field | type | notes |
|---|---|---|
| id | UUID PK | UUIDs everywhere except join tables — easier to share IDs across systems without leaking ordering |
| name | varchar(200) | |
| slug | varchar(50) unique | URL-safe identifier |
| created_at | timestamp | |

**`User`**
Custom user model (Django's `AbstractUser` extended). Each user belongs to exactly one
Organization in v1. (Multi-org users — a Breathe consultant working across clients — is
out of scope; called out in TRADEOFFS.md.)
| field | type | notes |
|---|---|---|
| organization_id | FK Organization | scoped |
| email | varchar unique | used as username |
| role | varchar choices('analyst', 'admin') | minimal RBAC |

---

## 2. Where things happen — `Facility`

A Facility is a physical or logical site where activity is consumed. A plant, an office
building, a vehicle fleet, "remote employees" as a bucket. We don't model meters or
sub-buildings — that's an analyst's bookkeeping concern, not a model concern.

| field | type | notes |
|---|---|---|
| id | UUID PK | |
| organization_id | FK Organization | scoped |
| name | varchar(200) | "Plant Frankfurt-3" |
| country | varchar(2) | ISO 3166-1 alpha-2; matters for grid emission factors for Scope 2 |
| facility_type | varchar choices('plant', 'office', 'fleet', 'other') | |

**`PlantCodeMapping`** — org-scoped lookup so the SAP ingester can resolve plant codes
(`DE03`, `4711`) to Facilities. Without this, the analyst sees raw plant codes in the
review queue and has to mentally translate. We populate it lazily: the first time we
see a new plant code, we create an unmapped row the admin must resolve.
| field | type | notes |
|---|---|---|
| id | UUID PK | |
| organization_id | FK Organization | scoped |
| sap_plant_code | varchar(10) | unique per org |
| facility_id | FK Facility nullable | null = unmapped, blocks ingestion |

---

## 3. The taxonomy — `ActivityType` and Scope 1/2/3

`ActivityType` is a controlled vocabulary of "things that emit." It carries the scope
classification and the canonical unit so that scope categorization happens at lookup
time, not at row time.

| field | type | notes |
|---|---|---|
| id | int PK | small fixed set, ~30 rows total |
| code | varchar(50) unique | 'diesel_combustion', 'electricity_grid', 'flight_economy' |
| name | varchar(200) | human label |
| scope | smallint choices(1,2,3) | the GHG Protocol classification |
| scope_category | varchar(50) | sub-category, e.g., '3.6 Business travel' |
| canonical_unit | varchar(20) | the unit we normalize to: 'liter', 'kwh', 'km', 'night' |

**Why `ActivityType` is a model and not an enum**: the scope and canonical unit are
properties of the activity, not of the record. Putting them on the record would
denormalize and let two rows of the same activity disagree on their scope, which is a
real audit hazard. Plus, new activities are added over time as Breathe onboards clients
with novel sources; an enum requires a code change and migration.

**Scope 1/2/3 specifically**: this is GHG Protocol. Scope 1 = direct combustion the org
owns (fleet fuel, on-site generators); Scope 2 = purchased energy (grid electricity,
purchased steam); Scope 3 = everything else in the value chain (business travel,
purchased goods, employee commute, etc.). The brief asks specifically for Scope 1/2/3
categorization, so the field is a hard requirement; the `scope_category` sub-field
exists because Scope 3 has 15 GHG Protocol sub-categories and auditors care about which
one (business travel = Cat 6, not Cat 7 employee commute, etc.).

---

## 4. Emission factors — `EmissionFactor`

| field | type | notes |
|---|---|---|
| id | UUID PK | |
| activity_type_id | FK ActivityType | |
| region | varchar(10) | country code, '*' = global default; lets grid factors vary by country |
| factor_kg_co2e_per_unit | decimal(12,6) | the multiplier; unit is the activity's canonical_unit |
| effective_from | date | inclusive |
| effective_to | date nullable | exclusive; null = currently active |
| source_citation | varchar(500) | "DEFRA 2024 GHG Conversion Factors, ICE Diesel (avg biofuel blend)" |

**Why factors are versioned by date**: when DEFRA updates the 2025 factors in mid-2025,
records computed in 2024 must keep their 2024-factor computation. Restatement (recomputing
old records with new factors) is a deliberate business decision an analyst makes, not
something that silently happens because we updated a row in a lookup table.

**Why a `source_citation` field**: auditors will ask "where did this number come from."
The factor is meaningless without provenance. This is the cheapest possible compliance
hook — one varchar.

**Why I separated `region` instead of one factor per country**: most factors don't vary
by country (diesel combustion chemistry is constant). Grid electricity does. A `region`
filter (with `*` fallback) keeps the table small. The lookup at compute-time prefers
exact country match, falls back to `*`.

---

## 5. The centerpiece — `EmissionRecord`

This is the row an analyst reviews. One per activity event.

| field | type | notes |
|---|---|---|
| id | UUID PK | |
| organization_id | FK Organization | scoped |
| facility_id | FK Facility nullable | nullable because some travel records have no facility |
| ingestion_batch_id | FK IngestionBatch | which upload produced this |
| activity_type_id | FK ActivityType | classification + canonical unit |
| period_start | date | activity period (e.g., billing period start) |
| period_end | date | activity period end |
| original_quantity | decimal(18,4) | what the source said |
| original_unit | varchar(20) | what the source said: 'US_gal', 'LTR', 'kWh', 'MWh' |
| quantity | decimal(18,4) | normalized to ActivityType.canonical_unit |
| unit | varchar(20) | always == ActivityType.canonical_unit; denormalized for query convenience |
| co2e_kg | decimal(18,4) | computed = quantity × EmissionFactor.factor_kg_co2e_per_unit |
| emission_factor_id | FK EmissionFactor | which factor was used (frozen at compute time) |
| status | varchar choices | pending / flagged / approved / locked / rejected |
| flag_reasons | JSONB | list of strings: ["unit_unknown", "value_3x_facility_median"] |
| source_payload | JSONB | original raw row from the source, untouched |
| source_row_identifier | varchar(200) | natural key in source system (e.g., SAP doc number); for dedup |
| created_at | timestamp | |
| created_by_id | FK User nullable | null = ingested by system, non-null = manually added |
| locked_at | timestamp nullable | non-null = immutable |
| locked_by_id | FK User nullable | |

**Six fields deserve their own justification**:

1. **`original_quantity`/`original_unit` AND `quantity`/`unit`** — both sides on every
   row. The brief asks for "unit normalization"; the way you prove normalization is
   honest is to show what was converted from what. An analyst reviewing a row sees
   "1500 L (original: 396.26 US gal)" and can sanity-check the conversion in their head.
   Without the original, "1500 L" is a number the analyst has to trust blindly.

2. **`source_payload` JSONB** — this is the "source-of-truth tracking" from the brief.
   The original raw row, untouched, even after we've normalized and computed. JSONB so
   we can index into it for debugging (`source_payload->>'Werk'` returns the SAP plant
   code as it appeared) without committing to a schema. The alternative — storing the
   uploaded file on disk — doesn't work on Render's free tier (no persistent disk) and
   couples forensic access to file storage. JSONB on the row is the right granularity:
   if someone asks "where did this 10,000 kWh come from?", we have the exact source row.

3. **`emission_factor_id` FK frozen at compute time** — when the factor table is later
   updated, this record's computation is unaffected. The factor used is forever
   attached to the record. Restatement is a separate operation that creates a new
   EmissionFactor and either re-runs computation (with audit) or doesn't.

4. **`source_row_identifier`** — natural key from the source. SAP rows have a doc
   number, utility rows have a bill ID, Concur rows have an expense report ID. Lets us
   detect "this is the same row we already ingested" on re-upload (dedup), and lets an
   analyst correlate back to the source system. Composite-unique with
   `(organization_id, ingestion_batch_id.source_system, source_row_identifier)`.

5. **`flag_reasons` JSONB** — a list of machine-detected issues (`unit_unknown`,
   `value_3x_facility_median`, `period_overlaps_existing`, `unmapped_plant_code`). JSONB
   not a related table because (a) the list is short and reads-only-with-record, and
   (b) we don't query "find all records flagged for X" often enough to justify a join.
   When we do, JSONB containment queries (`flag_reasons @> '["unit_unknown"]'`) are
   indexed and fast.

6. **`locked_at`/`locked_by_id`** as nullable — these are the audit lock. Once set,
   the row is immutable (enforced at the serializer + model `save()` level, plus a
   raw-SQL guard would be nice but is overkill for v1). Locked rows still appear in the
   review queue as read-only; deletion is never allowed once locked.

**Null semantics on `quantity`, `co2e_kg`, `emission_factor`**: these three are nullable
to honestly represent "we couldn't compute this." Storing zero would lie — the analyst
needs to know the difference between "this row consumed 0 kWh of grid electricity" (a
fact, co2e_kg = 0) and "this row's unit was 'therms', which we don't recognize"
(unknown, co2e_kg = null). The corresponding `flag_reasons` entry (`unit_unknown`,
`factor_missing`) tells the analyst what's wrong; the null tells the reporting layer
to exclude the row from totals until resolved. `original_quantity` and `original_unit`
are always populated because they come straight from the source — we never lose what
the source said.

---

## 6. Ingestion provenance — `IngestionBatch`

One row per "upload event." Carries the org-wide context that's shared across every
EmissionRecord in the batch.

| field | type | notes |
|---|---|---|
| id | UUID PK | |
| organization_id | FK Organization | scoped |
| source_system | varchar choices('sap', 'utility', 'travel') | |
| uploaded_by_id | FK User | |
| uploaded_at | timestamp | |
| original_filename | varchar(500) | |
| file_size_bytes | int | |
| file_sha256 | varchar(64) | dedup at the file level: re-upload of the exact same file is rejected |
| row_count_total | int | parsed |
| row_count_succeeded | int | created as EmissionRecord |
| row_count_failed | int | parse errors, see `parse_errors` |
| row_count_flagged | int | created but flagged for review |
| parse_errors | JSONB | list of {row_index, error_message, raw_row} |
| status | varchar choices('processing', 'completed', 'failed') | |

**Why a batch table and not just `uploaded_at` on the record**: an analyst working
through a review queue wants "show me all the rows from yesterday's utility upload" as
a single unit of work. The batch is the analyst's unit of cognition, not the row. Also,
parse errors don't have a corresponding EmissionRecord (the row failed to parse, so
there's nothing to attach the error to) — they need to live on the batch.

**Why `file_sha256`**: an analyst might re-upload the same file thinking it didn't go
through. We hash and reject duplicates at the file level. This is per-org — two
different orgs uploading the same hash is fine.

---

## 7. Audit trail — `EmissionRecordEdit`

Append-only log of every change to an EmissionRecord.

| field | type | notes |
|---|---|---|
| id | UUID PK | |
| emission_record_id | FK EmissionRecord | |
| edited_by_id | FK User | who made the change |
| edited_at | timestamp | |
| field_name | varchar(100) | which column was changed |
| old_value | text | stringified previous value |
| new_value | text | stringified new value |
| reason | text | analyst-provided rationale; mandatory on edits, optional on status changes |

**Why append-only and separate from the record**: an audit trail that can be mutated is
not an audit trail. Storing edits in a separate table with no DELETE permission for
application users is the cheapest path to integrity. The alternative (django-simple-history)
adds a shadow column to every model, includes a lot we don't need, and obscures what's
actually being tracked.

**Why `old_value`/`new_value` as text rather than typed columns**: edits cross types
(an analyst might change a `quantity` decimal, an `original_unit` varchar, or a
`status` choice). Untyped storage keeps the table simple; downstream readers know the
type by looking at `field_name`.

**Status changes are also edits**: changing `status` from `pending` to `approved` is an
`EmissionRecordEdit` row with `field_name='status'`, `old_value='pending'`,
`new_value='approved'`, `reason='Verified against utility bill PDF, dated 2025-08-31'`.
This means the lifecycle is fully reconstructable from the audit log.

---

## 8. Status lifecycle

```
                  ┌───────────────────────────┐
                  ▼                           │
  [ingestion] → pending ──→ approved ──→ locked  (terminal)
                  │
                  ├──→ flagged ──→ approved ──→ locked
                  │       │
                  │       └──→ rejected  (terminal, not deleted)
                  │
                  └──→ rejected  (terminal)
```

- **pending**: parsed and normalized successfully, awaiting human review
- **flagged**: parsed but the ingester or a downstream rule found something suspicious;
  surfaces in the analyst's "needs attention" queue before "routine" pending rows
- **approved**: analyst has reviewed and approved, but the period is not yet closed
- **locked**: the reporting period is closed and this row is now immutable evidence
- **rejected**: analyst determined the row is wrong (duplicate, garbage, miscategorized
  and can't be salvaged) — kept in the DB for audit, excluded from reports

**Why `rejected` is terminal and not deleted**: in an audit, "we threw it out" is a
better story than "it disappeared." A regulator can ask "show me everything that came
in from the August utility upload, including what you rejected and why." We can answer.

**Why `locked` is a separate state from `approved`**: approval is per-row, locking is
per-period (or per-batch, or per-fiscal-year — TBD by the analyst workflow). A row can
be approved but not yet locked because the period isn't closed. The lock is the
final "we sent this to the auditor" step.

---

## 9. What this model deliberately does NOT do

Listed here so reviewers can see the boundaries; full justification in TRADEOFFS.md.

- **Restatement workflow** — when factors change, recomputing locked rows with audit
  trail is its own subsystem. Out of scope.
- **Period definitions** — a `ReportingPeriod` table that defines fiscal years/quarters
  for batched locking. We treat locking as per-row.
- **Targets and baselines** — no SBTi targets, no baseline-year tracking, no progress
  reporting. This model holds the activity data; targets are a separate domain.
- **Scope 3.1 spend-based estimation** — would require an EEIO factor library
  (millions of factors keyed by industry classification × spend). Out of scope.
- **Inter-row consistency rules** — e.g., "total facility electricity for August must
  equal sum of meter readings." Possible as a future rule layer; v1 uses only per-row
  flags.
- **Multi-organization users** — a Breathe consultant who works across three client
  orgs. v1 is one user, one org.
- **Cascading restatement on factor edit** — editing an `EmissionFactor` does not
  recompute existing records. They're frozen via `emission_factor_id`. Intentional.

---

## 10. Indexes worth calling out

These aren't fully exhaustive; the migrations will have them, but the load-bearing
ones for the analyst dashboard are:

- `EmissionRecord(organization_id, status, period_end DESC)` — primary review queue
  query
- `EmissionRecord(organization_id, ingestion_batch_id)` — "show me everything from this
  upload"
- `EmissionRecord(organization_id, facility_id, period_start, period_end)` — "show me
  Plant Frankfurt-3 for Q3"
- GIN index on `EmissionRecord.flag_reasons` — for filtered "needs attention" views
- `EmissionRecord(organization_id, source_row_identifier)` UNIQUE-ish (with
  `ingestion_batch.source_system`) — dedup
- `EmissionFactor(activity_type_id, region, effective_from)` — factor lookup at compute
  time
