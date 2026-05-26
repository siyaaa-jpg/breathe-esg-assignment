// Record detail: original vs normalized, source payload, editable form,
// approve/reject/lock buttons, audit trail.

import { useCallback, useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { api, ApiError } from '../api'
import { FlagChips, StatusPill } from '../components/Pill'
import type { ActivityType, Facility, RecordDetail as RecordDetailT } from '../types'

export function RecordDetail() {
  const { id = '' } = useParams()
  const navigate = useNavigate()
  const [record, setRecord] = useState<RecordDetailT | null>(null)
  const [facilities, setFacilities] = useState<Facility[]>([])
  const [activities, setActivities] = useState<ActivityType[]>([])
  const [error, setError] = useState<string | null>(null)

  // Edit form state
  const [editing, setEditing] = useState(false)
  const [activityTypeId, setActivityTypeId] = useState<string>('')
  const [facilityId, setFacilityId] = useState<string>('')
  const [originalQuantity, setOriginalQuantity] = useState<string>('')
  const [originalUnit, setOriginalUnit] = useState<string>('')
  const [reason, setReason] = useState<string>('')
  const [busy, setBusy] = useState(false)

  const load = useCallback(() => {
    api
      .record(id)
      .then((r) => {
        setRecord(r)
        setActivityTypeId(r.activity_type ? String(r.activity_type.id) : '')
        setFacilityId(r.facility ? r.facility.id : '')
        setOriginalQuantity(r.original_quantity)
        setOriginalUnit(r.original_unit)
        setReason('')
      })
      .catch((e) => setError(e.message))
  }, [id])

  useEffect(() => {
    load()
    api.facilities().then(setFacilities)
    api.activityTypes().then(setActivities)
  }, [load])

  if (error) return <div className="error">{error}</div>
  if (!record) return <div className="loading">Loading…</div>

  const readonly = record.is_locked || record.status === 'rejected'

  async function saveEdit() {
    if (!reason.trim()) {
      setError('Reason is required')
      return
    }
    setError(null)
    setBusy(true)
    try {
      const changes: any = {}
      if (activityTypeId !== (record!.activity_type ? String(record!.activity_type.id) : '')) {
        changes.activity_type_id = activityTypeId ? Number(activityTypeId) : null
      }
      if (facilityId !== (record!.facility ? record!.facility.id : '')) {
        changes.facility_id = facilityId || null
      }
      if (originalQuantity !== record!.original_quantity) {
        changes.original_quantity = originalQuantity
      }
      if (originalUnit !== record!.original_unit) {
        changes.original_unit = originalUnit
      }
      if (Object.keys(changes).length === 0) {
        setError('No changes to save')
        return
      }
      const updated = await api.editRecord(id, changes, reason)
      setRecord(updated)
      setEditing(false)
      setReason('')
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : String(e)
      setError(msg)
    } finally {
      setBusy(false)
    }
  }

  async function doTransition(action: 'approve' | 'reject' | 'lock') {
    const why = window.prompt(`Reason for ${action} (optional):`) ?? ''
    setBusy(true)
    setError(null)
    try {
      const updated = await api.transitionRecord(id, action, why)
      setRecord(updated)
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : String(e)
      setError(msg)
    } finally {
      setBusy(false)
    }
  }

  // Which transitions are valid in the current state? mirrors emissions.services.TRANSITIONS
  const canApprove = record.status === 'pending' || record.status === 'flagged'
  const canReject = canApprove || record.status === 'approved'
  const canLock = record.status === 'approved'

  return (
    <>
      <div style={{ marginBottom: 16 }}>
        <a onClick={() => navigate(-1)} style={{ cursor: 'pointer' }}>← Back</a>
      </div>

      <h1>
        Record {record.source_row_identifier}{' '}
        <StatusPill status={record.status} />
      </h1>
      {record.flag_reasons.length > 0 && (
        <div style={{ marginBottom: 16 }}>
          <FlagChips flags={record.flag_reasons} />
        </div>
      )}
      {error && <div className="banner banner-error">{error}</div>}

      <div className="detail-grid">
        <div className="card detail-section">
          <h3>Original (from source)</h3>
          <div className="kv">
            <div className="k">Source</div>
            <div className="v">{record.ingestion_batch.source_system} — {record.ingestion_batch.original_filename}</div>
            <div className="k">Quantity</div>
            <div className="v">{record.original_quantity} {record.original_unit}</div>
            <div className="k">Period</div>
            <div className="v">{record.period_start} → {record.period_end}</div>
          </div>
        </div>

        <div className="card detail-section">
          <h3>Normalized + computed</h3>
          <div className="kv">
            <div className="k">Activity</div>
            <div className="v">
              {record.activity_type
                ? `${record.activity_type.code} (Scope ${record.activity_type.scope})`
                : <span className="muted">unclassified</span>}
            </div>
            <div className="k">Facility</div>
            <div className="v">
              {record.facility ? `${record.facility.name} (${record.facility.country})` : <span className="muted">unmapped</span>}
            </div>
            <div className="k">Quantity</div>
            <div className="v">
              {record.quantity ? `${record.quantity} ${record.unit}` : <span className="muted">not computed</span>}
            </div>
            <div className="k">CO₂e (kg)</div>
            <div className="v">{record.co2e_kg ?? <span className="muted">not computed</span>}</div>
            <div className="k">Factor</div>
            <div className="v">
              {record.emission_factor ? (
                <>
                  {record.emission_factor.factor_kg_co2e_per_unit} kg/{record.unit} ({record.emission_factor.region})
                  <div style={{ color: '#9ca3af', fontSize: 11, marginTop: 2 }}>
                    {record.emission_factor.source_citation}
                  </div>
                </>
              ) : (
                <span className="muted">none applied</span>
              )}
            </div>
          </div>
        </div>
      </div>

      <div className="card">
        <h3>Source payload (raw)</h3>
        <pre className="json">{JSON.stringify(record.source_payload, null, 2)}</pre>
      </div>

      {!readonly && (
        <div className="card">
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 12 }}>
            <h3 style={{ margin: 0 }}>Edit</h3>
            {!editing && (
              <button className="secondary" onClick={() => setEditing(true)}>
                Edit fields
              </button>
            )}
          </div>
          {editing && (
            <>
              <div className="field-row">
                <div className="field">
                  <label>Activity type</label>
                  <select value={activityTypeId} onChange={(e) => setActivityTypeId(e.target.value)}>
                    <option value="">— unclassified —</option>
                    {activities.map((a) => (
                      <option key={a.id} value={a.id}>
                        [S{a.scope}] {a.name}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="field">
                  <label>Facility</label>
                  <select value={facilityId} onChange={(e) => setFacilityId(e.target.value)}>
                    <option value="">— none —</option>
                    {facilities.map((f) => (
                      <option key={f.id} value={f.id}>
                        {f.name}
                      </option>
                    ))}
                  </select>
                </div>
              </div>
              <div className="field-row">
                <div className="field">
                  <label>Original quantity</label>
                  <input value={originalQuantity} onChange={(e) => setOriginalQuantity(e.target.value)} />
                </div>
                <div className="field">
                  <label>Original unit</label>
                  <input value={originalUnit} onChange={(e) => setOriginalUnit(e.target.value)} />
                </div>
              </div>
              <div className="field">
                <label>Reason (required)</label>
                <textarea value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Why are you changing this?" />
              </div>
              <div className="button-row">
                <button className="primary" onClick={saveEdit} disabled={busy}>Save</button>
                <button className="secondary" onClick={() => { setEditing(false); load(); }} disabled={busy}>Cancel</button>
              </div>
            </>
          )}
        </div>
      )}

      {!readonly && (
        <div className="card">
          <h3>Actions</h3>
          <div className="button-row">
            <button className="success" onClick={() => doTransition('approve')} disabled={!canApprove || busy}>
              Approve
            </button>
            <button className="primary" onClick={() => doTransition('lock')} disabled={!canLock || busy}>
              Lock (final)
            </button>
            <button className="danger" onClick={() => doTransition('reject')} disabled={!canReject || busy}>
              Reject
            </button>
          </div>
        </div>
      )}

      <div className="card">
        <h3>Audit trail ({record.edits.length})</h3>
        {record.edits.length === 0 ? (
          <div className="empty" style={{ padding: 16 }}>No edits yet.</div>
        ) : (
          <div className="audit-list">
            {record.edits.map((e) => (
              <div key={e.id} className="audit-row">
                <div>
                  <span className="who">{e.edited_by_email} at {new Date(e.edited_at).toLocaleString()}</span>
                </div>
                <div className="change">
                  {e.field_name}: <code>{e.old_value || '(empty)'}</code> → <code>{e.new_value || '(empty)'}</code>
                </div>
                {e.reason && <div className="reason">"{e.reason}"</div>}
              </div>
            ))}
          </div>
        )}
      </div>
    </>
  )
}
