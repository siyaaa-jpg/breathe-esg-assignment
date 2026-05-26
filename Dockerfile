# Two-stage build: Node compiles the React app, Python runs Django + gunicorn.
# Stage 1: build the frontend
FROM node:20-alpine AS frontend-builder
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci || npm install
COPY frontend/ ./
RUN npm run build

# Stage 2: Python runtime
FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DJANGO_SETTINGS_MODULE=config.settings.prod

WORKDIR /app

# Runtime libs needed by psycopg-binary
RUN apt-get update \
 && apt-get install -y --no-install-recommends libpq5 \
 && rm -rf /var/lib/apt/lists/*

# Python deps first (cached unless requirements.txt changes)
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt

# Project code
COPY backend/ /app/backend/
# Built React app — referenced by TEMPLATES.DIRS and STATICFILES_DIRS
COPY --from=frontend-builder /app/frontend/dist /app/frontend/dist

WORKDIR /app/backend

# Run migrations + seed at container start so a fresh Render Postgres comes
# online with reference data and a demo login. seed_* commands are idempotent.
# collectstatic also runs here (rather than at build) because the dev/prod
# settings split makes it cheaper to do once the env vars are present.
CMD sh -c "python manage.py collectstatic --noinput \
        && python manage.py migrate --noinput \
        && python manage.py seed_reference_data \
        && python manage.py seed_demo_org \
        && gunicorn config.wsgi:application --bind 0.0.0.0:${PORT:-8000} --workers 2 --access-logfile -"
