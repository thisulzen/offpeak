import { useState } from 'react'

export default function Upload({ onUploaded }: { onUploaded: (id: number) => void }) {
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  async function send(file: File) {
    setBusy(true)
    setError('')
    const res = await fetch('/api/files', { method: 'POST', body: file })
    setBusy(false)
    if (!res.ok) return setError((await res.json()).detail ?? 'Upload failed')
    onUploaded((await res.json()).file_id)
  }

  return (
    <section>
      <label>
        NEM12 file{' '}
        <input type="file" accept=".csv,.txt" disabled={busy} onChange={e => e.target.files?.[0] && send(e.target.files[0])} />
      </label>
      {busy && <p>Uploading…</p>}
      {error && <p role="alert">{error}</p>}
    </section>
  )
}
