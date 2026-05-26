# Tradeoffs

The brief asks for three things we deliberately did not build and why. Listing the
biggest cuts here, ordered by "reviewer is most likely to ask about this one first."
Smaller cuts are noted in DECISIONS.md.

## 1. PDF parsing of utility bills

**What a real version would do**: ingest scanned PDF utility bills via OCR + layout
parsing (e.g., Tesseract for OCR, then heuristics or a model to extract `kWh`,
`billing period`, `account number`, `tariff`). This is the most realistic single source
shape for utility data — facilities teams routinely have piles of PDF bills they need
to enter, not portal CSVs.

**What we built instead**: portal CSV upload only.

**Why we cut it**: PDF parsing is unreliable on a 4-day timeline. Layouts vary by
utility, by region, by year, by tariff plan. Building this and having it fail on the
reviewer's sample PDF is a worse outcome than not building it. We named the cut
explicitly in SOURCES.md and described what would break — PDF formats are
non-deterministic, OCR quality is highly variable, and "field extraction from utility
bills" is an actual product category (Bidgely, EnergyCAP) for good reason.

**What a real deployment would need**: a PDF ingestion pipeline using either a tuned
OCR + template library per utility, or a vision model with verification. Plus an
exception queue for PDFs that don't fit any template. Plus enough sample bills from
each utility to train the templates.

## 2. Real authentication, RBAC, and multi-org users

**What a real version would do**: OAuth/SSO integration (most enterprise clients
require it), granular role-based access (analyst-can-edit, analyst-can-approve-but-not-lock,
auditor-read-only, admin), and multi-org user support for Breathe consultants who work
across client accounts.

**What we built instead**: Django session auth with email/password, two roles
(`analyst`, `admin`), one user → one org.

**Why we cut it**: each of those features is its own non-trivial subsystem. SSO alone
is several days. The analyst review workflow — which is what the brief is actually
testing — doesn't depend on it. We made the cut explicit by mentioning multi-org users
in the MODEL.md "what this deliberately does not do" section.

**What a real deployment would need**: SAML/OIDC integration (probably via
django-allauth or python-social-auth), a permission system richer than role flags
(django-guardian or a custom one based on objects), and a User-Organization many-to-many
with a per-relationship role. The data model accommodates this — `Organization` is
already a top-level entity that User is FK'd to; the migration to many-to-many is
mechanical.

## 3. Background jobs / async ingestion

**What a real version would do**: a Celery worker (or RQ, or Django-Q) processing
uploaded files asynchronously. The user uploads, gets a batch ID, polls for status,
sees progress as rows stream in. Large SAP exports (10k+ rows) take minutes; doing
this in-request times out and locks up the web worker.

**What we built instead**: synchronous parsing. Upload → server parses entire file
→ returns batch summary. Works fine for files up to ~1000 rows. Falls over above
that — Render's free tier worker has a 30-second request timeout.

**Why we cut it**: Celery requires Redis (or RabbitMQ), which means another Render
service (another sleeping container, more memory pressure on the free tier), plus
worker configuration, plus a frontend polling state machine. We documented file size
expectations in SOURCES.md sample data sections.

**What a real deployment would need**: Celery + Redis on Render's paid tier, or a
managed queue (Render's background workers, AWS SQS, etc.). The data model already
has `IngestionBatch.status` with a `processing` value; the migration is mostly
moving the parser call into a `.delay()` and adding a polling endpoint to the API.
The frontend gets a progress bar instead of a synchronous response.
