"""
Ingestion has no models of its own — IngestionBatch lives in `emissions` because
it's a first-class data record with FK relationships to EmissionRecord. This
package holds the per-source parsing services in `services/`.

Kept as a Django app rather than a plain package so future evolution (e.g., a
SourceMapping table per org, or scheduled ingestion config) can land here
without restructuring.
"""
