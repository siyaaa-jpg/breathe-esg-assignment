# Breathe ESG — ingestion + analyst review prototype

A Django + React prototype that ingests emissions activity data from three
source systems (SAP fuel/procurement, utility electricity, corporate travel),
normalizes it, and surfaces a review dashboard where an analyst can see what
came in, what failed, what looks suspicious, and approve rows before they're
locked for audit.

## What to read first

The code is the second-most important thing in this repo. Read the docs in
this order:

1. **[MODEL.md](MODEL.md)** — the data model and the WHY behind every choice.
   Highest grading weight (35%). Start here.
2. **[SOURCES.md](SOURCES.md)** — for each of the three sources: what
   real-world format I researched, what the sample data looks like, what
   would break in a real deployment.
3. **[DECISIONS.md](DECISIONS.md)** — every ambiguity, what I chose, why,
   what I'd ask the PM if I could.
4. **[TRADEOFFS.md](TRADEOFFS.md)** — three things I deliberately did not
   build, and why.

## Deployment

Deployed to Render as a single Docker service (Django + built React) with a
managed Postgres database. See `Dockerfile` and `render.yaml` at the repo
root.

**Live URL**: https://breathe-esg-prototype-uqjy.onrender.com

**Demo login**: `admin@demo.test` / `demo12345` (seeded by the
`seed_demo_org` management command on first boot).

Free-tier caveat: the service sleeps after ~15 min idle; first request
after sleep takes 20-40s while the container starts.

### How to deploy
1. Push this repo to GitHub.
2. Connect the repo on Render. Render reads `render.yaml`, creates the
   web service + the managed Postgres, generates `DJANGO_SECRET_KEY`,
   and starts the first build.
3. First deploy takes ~5 min (Docker build + migrate + seed).

## Local quickstart

```bash
# Backend
cd backend
py -3.14 -m venv .venv                                     # or python -m venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
.venv/Scripts/python.exe manage.py migrate
.venv/Scripts/python.exe manage.py seed_reference_data    # ActivityTypes + EmissionFactors
.venv/Scripts/python.exe manage.py seed_demo_org          # Demo org + admin user + facilities + SAP mappings
.venv/Scripts/python.exe manage.py runserver

# Frontend (in another terminal)
cd frontend
npm install
npm run dev                                                # Vite at :5173, proxies /api etc to :8000
```

Visit:
- **Dev workflow**: http://localhost:5173 (Vite dev server with HMR; proxies API calls to Django at :8000)
- **Prod-style workflow**: `npm run build` in `frontend/`, then visit http://localhost:8000 — Django serves the React build

Log in via http://localhost:8000/admin/login/ with `admin@demo.test` /
`demo12345`. After login you're returned to the SPA.

## Trying it out

After login, three things to try:

1. **Upload page** → Upload `samples/utility_aug2025.csv` (source: utility).
   You'll see 3 rows succeed, 2 flagged, 2 failed. Click into the batch to
   see the records.
2. **Review queue** → Filter by status=flagged. Click a flagged record to
   see its detail.
3. **Record detail** → For the `unit_unknown` flag (the "therms" row),
   click "Edit fields", change unit to `kWh`, type a reason, save. Watch
   the flag clear, the record re-compute, and the status demote from
   `flagged` to `pending`. Then click Approve and Lock to walk through the
   lifecycle. Scroll down to see the audit trail.

Try the same with `samples/sap_aug2025.csv` (source: sap) and
`samples/travel_aug2025.json` (source: travel).

## Tests

```bash
cd backend
.venv/Scripts/python.exe manage.py test apps
# 39 tests, ~3s
```

The tests aren't aiming for line coverage — they're documentation-by-test
for the ingestion outcomes documented in MODEL.md (the seven row paths the
sample CSV exercises), the API contract, and the analyst edit/approve/lock
flow.

## Project structure

```
/
├── MODEL.md, SOURCES.md, DECISIONS.md, TRADEOFFS.md   ← read these
├── samples/                  ← realistic sample uploads
├── Dockerfile                ← two-stage build (Node → Python)
├── render.yaml               ← Render Infrastructure-as-Code
├── backend/
│   ├── config/               ← Django settings (base/dev/prod split)
│   ├── apps/
│   │   ├── accounts/         ← Organization, User
│   │   ├── emissions/        ← Facility, ActivityType, EmissionFactor,
│   │   │                       IngestionBatch, EmissionRecord, EmissionRecordEdit,
│   │   │                       PlantCodeMapping, MaterialCodeMapping, services
│   │   ├── ingestion/        ← BaseIngester + Utility/Sap/Travel parsers + IATA + units
│   │   └── api/              ← DRF views, serializers, management commands
│   └── requirements.txt
└── frontend/
    ├── src/
    │   ├── App.tsx, main.tsx, api.ts, types.ts, styles.css
    │   ├── components/Layout.tsx, Pill.tsx
    │   └── routes/Uploads.tsx, Review.tsx, RecordDetail.tsx
    └── vite.config.ts        ← dev: proxy to Django; build: base /static/
```

## Stack

- Backend: Django 5.2, DRF 3.16, Postgres (prod) / SQLite (dev), WhiteNoise, gunicorn
- Frontend: React 18, React Router 6, TypeScript, Vite, plain CSS (no Tailwind/component library)
- Deploy: Docker, Render
