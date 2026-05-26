// Thin fetch wrapper. Three jobs:
//   1. Include the CSRF token Django expects on unsafe methods
//   2. On 403 (not authenticated), redirect to /admin/login/ — reusing
//      Django's admin login UI saves us building one
//   3. Surface API error messages as throw'd ApiError so React can show them

import type {
  ActivityType,
  CurrentUser,
  Facility,
  IngestionBatch,
  Paginated,
  RecordDetail,
  RecordListItem,
} from './types'

const API_BASE = '/api'

export class ApiError extends Error {
  constructor(public status: number, message: string, public body?: any) {
    super(message)
  }
}

function getCsrfToken(): string {
  const match = document.cookie.match(/csrftoken=([^;]+)/)
  return match ? match[1] : ''
}

async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
  const isUnsafe = init?.method && init.method !== 'GET'
  const headers: HeadersInit = {
    'Content-Type': 'application/json',
    ...(init?.headers || {}),
  }
  if (isUnsafe) {
    ;(headers as Record<string, string>)['X-CSRFToken'] = getCsrfToken()
  }

  const res = await fetch(`${API_BASE}${path}`, {
    credentials: 'include',
    ...init,
    headers,
  })

  if (res.status === 403 && !init?.method) {
    // Not authenticated on a GET — bounce to Django admin login
    const next = encodeURIComponent(window.location.pathname + window.location.search)
    window.location.href = `/admin/login/?next=${next}`
    throw new ApiError(403, 'Not authenticated')
  }

  let body: any = null
  try {
    body = await res.json()
  } catch {
    /* no body */
  }

  if (!res.ok) {
    const message = (body && (body.error || body.detail)) || `HTTP ${res.status}`
    throw new ApiError(res.status, message, body)
  }
  return body as T
}

export const api = {
  me: () => fetchJson<CurrentUser>('/me/'),

  facilities: () => fetchJson<Facility[]>('/facilities/'),
  activityTypes: () => fetchJson<ActivityType[]>('/activity-types/'),

  batches: () => fetchJson<Paginated<IngestionBatch>>('/batches/'),

  records: (filters: Record<string, string>) => {
    const qs = new URLSearchParams(filters).toString()
    return fetchJson<Paginated<RecordListItem>>(`/records/${qs ? '?' + qs : ''}`)
  },
  record: (id: string) => fetchJson<RecordDetail>(`/records/${id}/`),
  editRecord: (
    id: string,
    changes: Partial<{
      activity_type_id: number | null
      facility_id: string | null
      original_quantity: string
      original_unit: string
      period_start: string
      period_end: string
    }>,
    reason: string,
  ) =>
    fetchJson<RecordDetail>(`/records/${id}/edit/`, {
      method: 'PATCH',
      body: JSON.stringify({ ...changes, reason }),
    }),
  transitionRecord: (id: string, action: 'approve' | 'reject' | 'lock', reason: string) =>
    fetchJson<RecordDetail>(`/records/${id}/transition/`, {
      method: 'POST',
      body: JSON.stringify({ action, reason }),
    }),

  upload: async (source: string, file: File): Promise<IngestionBatch> => {
    const fd = new FormData()
    fd.append('source', source)
    fd.append('file', file)
    const res = await fetch(`${API_BASE}/uploads/`, {
      method: 'POST',
      credentials: 'include',
      headers: { 'X-CSRFToken': getCsrfToken() },
      body: fd,
    })
    let body: any = null
    try {
      body = await res.json()
    } catch {
      /* no body */
    }
    if (!res.ok) {
      throw new ApiError(res.status, (body && body.error) || `Upload failed (${res.status})`, body)
    }
    return body as IngestionBatch
  },
}
