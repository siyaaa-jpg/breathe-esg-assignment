// Uploads page: file picker + recent batches list. Clicking a batch
// navigates to the review queue filtered to that batch.

import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, ApiError } from '../api'
import { StatusPill } from '../components/Pill'
import type { IngestionBatch } from '../types'

export function Uploads() {
  const navigate = useNavigate()
  const [source, setSource] = useState<'utility' | 'sap' | 'travel'>('utility')
  const [file, setFile] = useState<File | null>(null)
  const [uploading, setUploading] = useState(false)
  const [message, setMessage] = useState<{ kind: 'success' | 'error'; text: string } | null>(null)
  const [batches, setBatches] = useState<IngestionBatch[]>([])

  function reload() {
    api.batches().then((b) => setBatches(b.results))
  }
  useEffect(reload, [])

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!file) return
    setUploading(true)
    setMessage(null)
    try {
      const batch = await api.upload(source, file)
      setMessage({
        kind: 'success',
        text: `Uploaded ${file.name}: ${batch.row_count_succeeded} ok, ${batch.row_count_flagged} flagged, ${batch.row_count_failed} failed.`,
      })
      setFile(null)
      ;(document.getElementById('upload-file-input') as HTMLInputElement).value = ''
      reload()
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : String(e)
      setMessage({ kind: 'error', text: msg })
    } finally {
      setUploading(false)
    }
  }

  return (
    <>
      <h1>Uploads</h1>

      <div className="card">
        <h2 style={{ marginTop: 0 }}>Upload a source file</h2>
        <form onSubmit={handleSubmit}>
          <div className="field-row">
            <div className="field">
              <label>Source system</label>
              <select value={source} onChange={(e) => setSource(e.target.value as any)}>
                <option value="utility">Utility (CSV)</option>
                <option value="sap">SAP (CSV)</option>
                <option value="travel">Corporate travel (JSON)</option>
              </select>
            </div>
            <div className="field">
              <label>File</label>
              <input
                id="upload-file-input"
                type="file"
                onChange={(e) => setFile(e.target.files?.[0] || null)}
              />
            </div>
          </div>
          {message && <div className={`banner banner-${message.kind}`}>{message.text}</div>}
          <button type="submit" className="primary" disabled={!file || uploading}>
            {uploading ? 'Uploading…' : 'Upload'}
          </button>
        </form>
      </div>

      <h2>Recent batches</h2>
      {batches.length === 0 ? (
        <div className="empty">No uploads yet.</div>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Source</th>
              <th>File</th>
              <th>Uploaded</th>
              <th>By</th>
              <th className="num">Total</th>
              <th className="num">Ok</th>
              <th className="num">Flagged</th>
              <th className="num">Failed</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {batches.map((b) => (
              <tr
                key={b.id}
                className="clickable"
                onClick={() => navigate(`/review?batch=${b.id}`)}
              >
                <td>{b.source_system}</td>
                <td>{b.original_filename}</td>
                <td>{new Date(b.uploaded_at).toLocaleString()}</td>
                <td>{b.uploaded_by_email}</td>
                <td className="num">{b.row_count_total}</td>
                <td className="num">{b.row_count_succeeded}</td>
                <td className="num">{b.row_count_flagged}</td>
                <td className="num">{b.row_count_failed}</td>
                <td>
                  <StatusPill status={b.status} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  )
}
