// Review queue: filterable, paginated table of EmissionRecords.

import { useEffect, useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { api } from '../api'
import { FlagChips, StatusPill } from '../components/Pill'
import type { ActivityType, Facility, Paginated, RecordListItem } from '../types'

const STATUSES = ['pending', 'flagged', 'approved', 'locked', 'rejected']
const SOURCES = ['utility', 'sap', 'travel']

export function Review() {
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const [data, setData] = useState<Paginated<RecordListItem> | null>(null)
  const [loading, setLoading] = useState(false)
  const [facilities, setFacilities] = useState<Facility[]>([])
  const [activities, setActivities] = useState<ActivityType[]>([])

  // Filter state derived from URL search params
  const filters: Record<string, string> = useMemo(() => {
    const out: Record<string, string> = {}
    for (const k of ['status', 'source', 'batch', 'facility', 'activity_type', 'page']) {
      const v = searchParams.get(k)
      if (v) out[k] = v
    }
    return out
  }, [searchParams])

  useEffect(() => {
    api.facilities().then(setFacilities)
    api.activityTypes().then(setActivities)
  }, [])

  useEffect(() => {
    setLoading(true)
    api
      .records(filters)
      .then(setData)
      .finally(() => setLoading(false))
  }, [filters])

  function updateFilter(key: string, value: string) {
    const next = new URLSearchParams(searchParams)
    if (value) next.set(key, value)
    else next.delete(key)
    next.delete('page') // reset paging on filter change
    setSearchParams(next, { replace: true })
  }

  return (
    <>
      <h1>Review queue</h1>

      <div className="filters">
        <div className="field">
          <label>Status</label>
          <select value={filters.status || ''} onChange={(e) => updateFilter('status', e.target.value)}>
            <option value="">All</option>
            {STATUSES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label>Source</label>
          <select value={filters.source || ''} onChange={(e) => updateFilter('source', e.target.value)}>
            <option value="">All</option>
            {SOURCES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label>Facility</label>
          <select value={filters.facility || ''} onChange={(e) => updateFilter('facility', e.target.value)}>
            <option value="">All</option>
            {facilities.map((f) => (
              <option key={f.id} value={f.id}>
                {f.name}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label>Activity</label>
          <select
            value={filters.activity_type || ''}
            onChange={(e) => updateFilter('activity_type', e.target.value)}
          >
            <option value="">All</option>
            {activities.map((a) => (
              <option key={a.id} value={a.id}>
                [S{a.scope}] {a.name}
              </option>
            ))}
          </select>
        </div>
        {filters.batch && (
          <div className="field">
            <label>Filtered to batch</label>
            <button className="secondary" onClick={() => updateFilter('batch', '')}>
              Clear batch filter
            </button>
          </div>
        )}
      </div>

      {loading && <div className="loading">Loading…</div>}
      {!loading && data && data.results.length === 0 && (
        <div className="empty">No records match these filters.</div>
      )}
      {!loading && data && data.results.length > 0 && (
        <>
          <table>
            <thead>
              <tr>
                <th>Source</th>
                <th>Facility</th>
                <th>Activity</th>
                <th>Period</th>
                <th className="num">Quantity</th>
                <th className="num">CO₂e (kg)</th>
                <th>Status</th>
                <th>Flags</th>
              </tr>
            </thead>
            <tbody>
              {data.results.map((r) => (
                <tr key={r.id} className="clickable" onClick={() => navigate(`/records/${r.id}`)}>
                  <td>{r.source_system}</td>
                  <td>{r.facility_name || <span className="muted">—</span>}</td>
                  <td>
                    {r.activity_code ? (
                      <>
                        {r.activity_code}
                        {r.activity_scope && <span style={{ color: '#9ca3af', marginLeft: 4 }}>S{r.activity_scope}</span>}
                      </>
                    ) : (
                      <span className="muted">unclassified</span>
                    )}
                  </td>
                  <td>
                    {r.period_start === r.period_end
                      ? r.period_start
                      : `${r.period_start} → ${r.period_end}`}
                  </td>
                  <td className="num">
                    {r.quantity ? (
                      <>
                        {r.quantity} {r.unit}
                      </>
                    ) : (
                      <span className="muted">— ({r.original_quantity} {r.original_unit})</span>
                    )}
                  </td>
                  <td className="num">
                    {r.co2e_kg ? Number(r.co2e_kg).toFixed(2) : <span className="muted">—</span>}
                  </td>
                  <td>
                    <StatusPill status={r.status} />
                  </td>
                  <td>
                    <FlagChips flags={r.flag_reasons} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="pagination">
            <span className="count">{data.count} records total</span>
          </div>
        </>
      )}
    </>
  )
}
