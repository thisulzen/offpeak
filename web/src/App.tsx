import { useState } from 'react'
import Upload from './Upload'
import Results from './Results'

export default function App() {
  const [fileId, setFileId] = useState<number | null>(null)
  return (
    <main>
      <h1>offpeak</h1>
      <p className="lede">
        Upload a year of smart-meter data (a NEM12 file from your retailer or distributor) and see what each
        retail plan would have charged you.
      </p>
      {fileId === null ? <Upload onUploaded={setFileId} /> : <Results fileId={fileId} onReset={() => setFileId(null)} />}
    </main>
  )
}
