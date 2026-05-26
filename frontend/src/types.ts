// API response shapes. Hand-maintained to match backend serializers; if a
// field is added on the backend, mirror it here. Small enough that a code-gen
// step isn't worth the dependency.

export interface Organization {
  id: string
  name: string
  slug: string
}

export interface CurrentUser {
  id: string
  email: string
  full_name: string
  role: 'analyst' | 'admin'
  organization: Organization
}

export interface Facility {
  id: string
  name: string
  country: string
  facility_type: 'plant' | 'office' | 'fleet' | 'other'
}

export interface ActivityType {
  id: number
  code: string
  name: string
  scope: 1 | 2 | 3
  scope_category: string
  canonical_unit: string
}

export interface EmissionFactor {
  id: string
  activity_code: string
  region: string
  factor_kg_co2e_per_unit: string
  effective_from: string
  effective_to: string | null
  source_citation: string
}

export type RecordStatus = 'pending' | 'flagged' | 'approved' | 'locked' | 'rejected'

export interface IngestionBatch {
  id: string
  source_system: 'sap' | 'utility' | 'travel'
  uploaded_by_email: string
  uploaded_at: string
  original_filename: string
  file_size_bytes: number
  file_sha256: string
  row_count_total: number
  row_count_succeeded: number
  row_count_flagged: number
  row_count_failed: number
  parse_errors: Array<{ row_index: number; message: string; raw_row: any }>
  status: 'processing' | 'completed' | 'failed'
}

export interface RecordListItem {
  id: string
  source_system: string
  facility_name: string | null
  activity_code: string | null
  activity_scope: number | null
  period_start: string
  period_end: string
  original_quantity: string
  original_unit: string
  quantity: string | null
  unit: string
  co2e_kg: string | null
  status: RecordStatus
  flag_reasons: string[]
}

export interface RecordEdit {
  id: string
  edited_by_email: string
  edited_at: string
  field_name: string
  old_value: string
  new_value: string
  reason: string
}

export interface RecordDetail {
  id: string
  activity_type: ActivityType | null
  facility: Facility | null
  ingestion_batch: IngestionBatch
  emission_factor: EmissionFactor | null
  period_start: string
  period_end: string
  original_quantity: string
  original_unit: string
  quantity: string | null
  unit: string
  co2e_kg: string | null
  status: RecordStatus
  flag_reasons: string[]
  source_payload: Record<string, any>
  source_row_identifier: string
  created_at: string
  is_locked: boolean
  locked_at: string | null
  locked_by_email: string | null
  edits: RecordEdit[]
}

export interface Paginated<T> {
  count: number
  next: string | null
  previous: string | null
  results: T[]
}
