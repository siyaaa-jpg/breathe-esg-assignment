# Sources

For each of the three sources: what we researched, what we learned, what our sample
data looks like and why, and what would break in real-world deployment. Filled in
as each source ingester is built.

---

## 1. SAP — fuel and procurement

### What real-world format we researched

SAP data leaves an SAP system through one of five mechanisms:

1. **IDoc (Intermediate Document)**: SAP's native EDI format. Looks like
   a structured flat-file with segment tags (E1MARAM, E1MARCM for materials;
   E1MBEWM for valuations). The standard format for B2B and inter-system
   transfers. Requires schema knowledge per IDoc type; complex parser.
2. **BAPI / RFC**: function-call API into a live SAP system. Use `pyrfc`
   or the SAP Java Connector + a JVM bridge. Needs SAP credentials and a
   running SAP system to test against.
3. **OData service**: REST endpoint exposed by S/4HANA on `/sap/opu/odata/`.
   Newer, more developer-friendly, but requires the service to be activated
   on the SAP side and credentials to call it.
4. **Flat-file CSV/XLSX export**: from SAP GUI's "Export to Excel" on any
   transaction (MB51 for material document list, ME2N for purchase orders),
   or from a custom ABAP report that emits CSV/XML. This is what almost
   every sustainability team actually receives because they can't get IT
   to plumb live feeds for them.
5. **Tax-audit flat files**: GDPdU (Germany) or DART (US). Highly structured
   per regulatory format; less likely for our use case but the most
   reliable shape when available.

For Fuel/Procurement specifically, the relevant SAP modules are:
- **MM (Materials Management)**: goods receipts (MIGO transaction, movement
  type 101), invoice receipts (MIRO), purchase orders (ME21N).
- **CO/PP (Cost / Production Planning)**: material consumption postings
  (movement types 261, 311), cost-center allocations.

A typical fuel posting in MM/CO has columns like: Werk (plant), Material
(SKU), Materialkurztext (description), Buchungsdatum (posting date),
Belegnummer (document number), Menge (quantity), Mengeneinheit (unit of
measure), Kostenstelle (cost center).

Sources I read: SAP Help Portal on IDoc INVOIC and ORDERS, S/4HANA OData
service documentation, MB51 transaction documentation, and the Movement
Type table (MSEG / MKPF).

### Why this shape

I picked **flat-file CSV** (mimic of SAP GUI "Export to Excel" or a custom
ABAP report). Three reasons:

1. **It's what actually shows up in practice.** Sustainability teams almost
   never get IT to plumb OData; they get a colleague to download the MB51
   export and email it. Building for IDoc/BAPI without an actual SAP system
   to test against would be theater.
2. **It exercises real SAP quirks** without needing SAP itself: mixed
   German/English headers (Werk is rarely localized to English even in
   English SAP), DD.MM.YYYY dates, German decimal commas, semicolon
   delimiters (German Excel default), and the material-code-to-activity
   indirection that no SAP system can resolve on its own.
3. **It generalizes**: when a client later switches to OData or BAPI, the
   downstream model is the same — we'd swap the parser, not the schema.

### What our sample data looks like

[`samples/sap_aug2025.csv`](samples/sap_aug2025.csv) — 7 rows, semicolon
delimited (German Excel default), DD.MM.YYYY dates. Mix of German and
English-aliased headers handled by the parser.

| Row | What it shows |
|---:|---|
| 1 | DE03 (Frankfurt) + FUEL-DIESEL-001 + 15000 L → mapped both ways → `pending`, computed |
| 2 | DE03 + FUEL-GASOLINE-002 + 850 L → `pending` |
| 3 | US01 (Dallas) + same diesel + 3000 **USG** (US gallons) → normalized to ~11356 L → `pending` |
| 4 | DE03 + **UNKNOWN-MAT-X** (no MaterialCodeMapping) → `flagged` with `material_unknown`, activity/quantity/co2e all null. A placeholder MaterialCodeMapping row is lazy-created so admin sees it. |
| 5 | **DE99** (no PlantCodeMapping) + mapped material → `flagged` with `facility_unknown`, uses global diesel factor. Placeholder PlantCodeMapping created. |
| 6 | DE03 + FUEL-NATURAL-GAS-001 + 2500 M3 → `pending`, Scope 1 stationary natural gas |
| 7 | "invalid-date" → hard error |

The plant codes (`DE03`, `US01`) and material codes (`FUEL-DIESEL-001`,
etc.) are made-up but follow SAP conventions — plant codes are short
alphanumeric (2-4 chars typically), material codes are upper-case with
hyphens/underscores. Real SAP plant codes are often 4-digit numbers
(`4711`, `1010`); I used readable identifiers for the demo so the analyst
context is obvious.

### What would break in real deployment

- **Plant/material mapping is org-specific and labor-intensive.** A real
  client has hundreds of materials. Our lazy-creation pattern surfaces them
  one at a time in the admin; that's right for the prototype but a real
  onboarding needs a bulk-import CSV for MaterialCodeMapping and probably
  a fuzzy-match suggestion engine ("this looks like the diesel material
  you mapped last month").
- **No support for movement-type semantics.** SAP's movement type 101
  (goods receipt) vs 261 (consumption) vs 311 (transfer) means different
  things for emissions accounting (e.g., a 311 isn't a Scope 1 event, just
  a logistics movement). We treat every row as a consumption event. A real
  parser needs movement-type rules and ought to ignore non-emissions
  postings.
- **No procurement spend → Scope 3 estimation.** Real procurement data
  (purchase orders, invoices) is the primary feedstock for Scope 3.1
  (purchased goods and services), via EEIO (environmentally-extended
  input-output) factor libraries keyed by industry classification (UNSPSC,
  NAICS). We model fuel procurement only — anything else gets material_unknown.
- **No multi-currency or cost ingestion.** Real procurement rows have a
  monetary amount; we ignore it. A reporting layer that does spend-based
  estimation needs FX conversion at the posting date.
- **No reversal handling.** SAP can reverse a posting (creates a movement
  type 102 = reversal of 101). We'd ingest both and double-count. A real
  parser needs to detect reversals and net them out before record creation.
- **Plant-code reuse across orgs.** Two different SAP clients can both
  have a plant `DE03` meaning entirely different sites. Our PlantCodeMapping
  is org-scoped, which handles this — but it does mean the same code in
  two orgs is a coincidence, not a join.
- **Encoding edge cases.** German SAP exports are sometimes Latin-1
  encoded (cp1252). We only handle utf-8 (with BOM tolerance). Real
  ingestion needs encoding detection.

---

## 2. Utility — electricity

### What real-world format we researched

Utility data reaches a sustainability team through one of four mechanisms,
ordered by how often we expect each in real-world clients:

1. **PDF bills**: scanned or vector PDFs of monthly statements. Highest volume
   in practice — a typical mid-sized client gets dozens per month from various
   regional utilities. Layouts vary wildly: ConEd, PG&E, National Grid, Centrica
   (UK), E.ON (DE) all use different page structures. Common fields: account
   number, service address, billing period, total kWh, demand kW, tariff line
   items, cost. OCR + per-utility template extraction (or a vision model) is
   the standard productionized approach.
2. **Portal CSV exports**: facility manager logs into the utility's customer
   portal and downloads a monthly usage summary. Common with US utilities
   (PG&E "Usage Details", ConEd Green Button "Usage Data"). Shape is one row
   per meter per billing period with kWh, cost, tariff class.
3. **Interval (load profile) data**: 15-minute or hourly meter reads, either
   from a smart meter via Green Button XML/CSV, or from a separate metering
   service (e.g., Wattics, Bidgely). Thousands of rows per month per meter.
   Used by energy engineers for demand-side management, less so for emissions
   reporting where monthly granularity is enough.
4. **Direct API integrations**: some utilities and aggregators expose APIs
   (Utility API, Arcadia). Authentication varies (OAuth, signed requests,
   account-level credentials), endpoint shapes differ entirely between
   providers. Coverage is patchy — only US and select EU utilities.

I read documentation for: ConEd Green Button Connect (XML and CSV variants),
PG&E Share My Data, EnergyStar Portfolio Manager's data import templates,
and the Utility API documentation. The PortfolioManager template is the
closest thing to a normalized "this is what consultants expect" shape
across utilities — it's tabular CSV with monthly summaries and is what I
used as the model for our sample shape.

### Why this shape

I picked the **portal CSV bill-summary** shape. Three reasons:

1. **It's what facilities managers actually use** for monthly carbon
   reporting. They aren't pulling interval data unless they're an energy
   engineer; they're downloading the same "Usage Details" PDF or CSV they
   submit to accounting.
2. **It's deterministic to parse**. Real PDF parsing is templated per utility
   and unreliable — building it on a 4-day timeline and having it misread the
   reviewer's sample is a worse outcome than not building it. See TRADEOFFS.md.
3. **It exercises the model meaningfully**. The shape forces unit normalization
   (kWh vs MWh), facility resolution (service_address → Facility), and
   billing-period handling that doesn't align to calendar months. Those are
   the model behaviors that matter; the file format is just the vehicle.

### What our sample data looks like

[`samples/utility_aug2025.csv`](samples/utility_aug2025.csv) — 7 rows
chosen to demonstrate every ingestion path:

| Row | What it shows |
|---:|---|
| 1 | Clean kWh row, known facility → `pending`, computed with German grid factor (0.3801 kgCO2e/kWh) |
| 2 | Same facility, **unit=MWh** → normalized 2.85 × 1000 = 2850 kWh, `pending` |
| 3 | Different facility (Munich Office) → `pending` |
| 4 | **Unknown facility** ("Unknown Site") → `flagged` with `facility_unknown`, falls back to global IEA factor (0.475) |
| 5 | **Unknown unit** ("therms") → `flagged` with `unit_unknown`, `quantity`/`co2e_kg` null |
| 6 | **Inverted period** (start > end) → hard error, lands in `parse_errors`, no record created |
| 7 | **Negative kWh** → hard error |

Sample columns (`account_number`, `meter_id`, `service_address`,
`billing_period_start`, `billing_period_end`, `total_kwh`, `unit`,
`tariff_class`, `total_cost`, `currency`) match what's in PortfolioManager's
"Bulk Upload of New Meters and Meter Data" template, minus the fields that
aren't emissions-relevant.

Sample data uses **DE** facilities deliberately so the factor lookup
demonstrates region-specific selection (DE grid factor 0.3801, not the
global 0.475). The Aug 2025 period is recent enough that the 2024-vintage
DEFRA/EPA/EEA factors are still in effect (no `effective_to` set).

### What would break in real deployment

- **PDF utility bills** are the dominant real shape; we don't handle them.
  Production needs OCR + per-utility template extraction or a vision model.
  See TRADEOFFS.md §1.
- **Multi-tariff bills** (peak/off-peak rates with different kWh totals) get
  collapsed by our schema into a single `total_kwh`. Real reporting may want
  per-tariff splits for accuracy when the utility offers a low-carbon
  off-peak rate.
- **Non-kWh metering**: gas meters report in `m3` or `therms`. Our
  electricity-focused activity type can't accept those. Adding `natural_gas`
  to the utility ingester is mechanical but not done.
- **Billing-period vs reporting-period alignment**: we keep the billing
  period dates exactly as the utility gives them (e.g., 2025-07-28 to
  2025-08-27). When a downstream report wants "August consumption," it needs
  to allocate the partial-month overlap proportionally. That allocation
  belongs in the reporting layer, not ingestion — but if the reporting layer
  doesn't do it, totals will be wrong.
- **Encoding edge cases**: utf-8-sig handles BOM but not Latin-1 or Windows
  1252, which some old utility portals still emit. A real implementation
  needs `chardet` or a fallback path.
- **Currency-mixed cost columns**: we ignore costs entirely. A real system
  that wants to surface cost-per-tCO2e needs FX conversion at the right date.
- **No re-upload reconciliation**: SHA256 dedup at the file level is brittle.
  If a utility re-issues a corrected bill (new PDF, same period), our
  duplicate detection fires on the row level (`duplicate_candidate` flag)
  but doesn't know which is "correct." A real system needs a supersede flow.

---

## 3. Corporate travel

### What real-world format we researched

Three dominant platforms for enterprise corporate travel:

1. **SAP Concur**: market leader. REST API exposes Expense Reports v4 at
   `/expense/reports/{id}` returning JSON with line items. Each item has
   `expense_type` (configurable per-customer, but follows common categories
   like Airfare, Hotel, Car Rental, Taxi, Meals), amount, currency,
   transaction date, plus category-specific fields. Concur also has a
   separate `ItineraryService` API for structured flight segments
   (origin/destination, airline, flight number, fare class) that's distinct
   from expense reports.
2. **Navan (formerly TripActions)**: newer, API-first. Trips API returns
   structured trip data with better airport-code and distance fields out
   of the box. Less mature than Concur but cleaner shapes.
3. **Egencia, Travelport, Sabre**: corporate GDS-style platforms. Use
   BSP/ARC reporting feeds or proprietary integrations. Less structured;
   often delivered as flat CSV.

For emissions reporting, the Concur Expense Reports shape is what most
implementations target — it's the lowest common denominator, it includes
cost (useful for FX-converted spend-based fallback), and most clients
have it. Concur also lacks distance for flights in the expense item
(you typically only get origin/destination airports), which is why we
compute distance from IATA codes.

Sources I read: SAP Concur "Expense Reports v4" API reference, Concur's
"Travel Itinerary v1.1" docs, and Navan's "Trips API" docs.

### Why this shape

I picked **JSON upload mimicking a simplified Concur Expense Reports
payload**. Three reasons:

1. **Travel platforms expose JSON**. CSV from a travel platform is rare
   (you might get one for ground transportation receipts, but not as the
   primary feed). JSON matches what a Python script calling the Concur
   API would receive.
2. **JSON's nested-array shape fits expense reports naturally**: one
   report header, a list of line items. CSV would force us to denormalize
   awkwardly or use multi-row joins.
3. **It lets us demonstrate the IATA → distance computation**, which is
   the most travel-specific piece of logic and the most realistic part
   of the modeling for this source. (A real Concur API doesn't give you
   distance — you compute it.)

The `cabin_class` field is included because business/first-class flights
have meaningfully different per-passenger-km factors in reality (factor
of ~3x for long-haul business vs economy due to lower passenger density
per seat). Our seed only has economy factors; the parser flags upgrades
with `cabin_class_upgrade` so the analyst can adjust manually.

### What our sample data looks like

[`samples/travel_aug2025.json`](samples/travel_aug2025.json) — one
expense report with 6 items demonstrating every code path:

| Item | What it shows |
|---:|---|
| 1 | FRA → JFK economy → distance computed (~6200 km) → `flight_long_haul` → `pending` |
| 2 | JFK → FRA business → same distance, **business class** → `flagged` with `cabin_class_upgrade`, still uses economy long-haul factor |
| 3 | Hotel, 5 nights, US → `hotel_night` × 5, US region → `pending` |
| 4 | Ground, taxi, 18.5 km → `taxi_ride` → `pending` |
| 5 | Ground, rental car, 220 km → `rental_car` → `pending` |
| 6 | XYZ → JFK (XYZ is not a real IATA) → `flagged` with `airport_unknown`, activity/co2e null |

The `report_id` + per-item `ticket_number` / `ref` keys give a deterministic
`source_row_identifier` (`ER-2025-08-001|T-001` etc.) so re-uploading the
same payload would trigger our duplicate_candidate detection.

The cost/currency fields are kept in `source_payload` but ignored at
compute time — we don't do spend-based travel estimation in v1.

### What would break in real deployment

- **IATA airport database is tiny (~35 airports).** A real version uses
  the full openflights.org dataset (~7000 airports) or a commercial feed
  like OAG. Anything outside our 35 → `airport_unknown` flag, requiring
  manual analyst entry.
- **No cabin class factor differentiation.** Real factor libraries
  (DEFRA, ICAO) publish separate factors for economy / premium economy /
  business / first per haul length. We have economy only and flag the
  rest. Production needs at least the full DEFRA business-travel matrix.
- **Multi-passenger flights** (multiple employees on the same flight)
  aren't modeled. Our `original_unit="passenger_km"` assumes 1 passenger
  per row. Real Concur expense items are one-employee-per-item, so this
  matches expense data — but a real travel API integration (Itinerary
  Service) gives flight-level data that needs multiplication by passenger
  count.
- **No support for radius-of-flight ("RF") or distance-adjusted factors.**
  ICAO methodology adds an uplift to great-circle distance to account for
  non-direct routes; DEFRA applies different RF coefficients per haul
  bucket. We use raw great-circle. For aviation accuracy this matters at
  ~10% scale.
- **No train/rail support.** Common in EU business travel and lower-carbon
  than flight; a real version needs it as a separate activity type with
  per-country grid factors (electric trains follow grid emissions).
- **Hotel factors are global average.** Hotel emissions vary by country
  (cleaner grids → cleaner hotels) and by hotel class. We use a single
  global per-night factor (Cornell Hotel Sustainability Benchmark). Real
  systems use per-country tables (Cornell Index or Hilton's GREB).
- **No expense-to-trip joining.** A real integration links expense items
  to a Travel Itinerary so a single flight's expense + booking + GHG
  output are unified. We treat each expense item independently.
- **No FX conversion.** Cost fields are stored raw with their currency.
  Cost-per-tCO2e analysis needs FX rates at the transaction date.
