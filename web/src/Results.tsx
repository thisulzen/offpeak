import { useEffect, useState } from 'react'

type Quote = { id: number; retailer: string; name: string; tariff_type: string; annual_cost_cents: number }
type Result = {
  state: string
  nmi: string | null
  dq: { summary: string[]; count: number }
  plans: { total: number; priceable: number; retailers: number; source: string }
  quotes?: Quote[]
  usage_by_hour?: { hour: number; kwh: number }[]
  battery?: { saving_per_year: number; payback_years: number | null; cost: number }
}
type Bill = { extractor: string; annual_cost_cents: number | null; fields: Record<string, string | number | null> }

const FINISHED = ['done', 'quarantined', 'failed']
const dollars = (cents: number) => '$' + Math.round(cents / 100).toLocaleString('en-AU')

export default function Results({ fileId, onReset }: { fileId: number; onReset: () => void }) {
  const [r, setR] = useState<Result | null>(null)

  useEffect(() => {
    let stop = false
    async function poll() {
      const next: Result = await (await fetch(`/api/files/${fileId}`)).json()
      if (stop) return
      setR(next)
      if (!FINISHED.includes(next.state)) setTimeout(poll, 1000)
    }
    poll()
    return () => { stop = true }
  }, [fileId])

  if (!r) return <p>Loading…</p>
  if (!FINISHED.includes(r.state)) return <p>Working on it: {r.state}…</p>

  return (
    <>
      <button onClick={onReset}>Upload another file</button>
      {r.state === 'quarantined' && (
        <p role="alert">This file has too many gaps to price fairly, so we haven't. Here's what we found:</p>
      )}
      {r.state === 'failed' && <p role="alert">Something went wrong processing this file.</p>}
      {r.quotes && r.quotes.length > 0 && <Plans quotes={r.quotes} fileId={fileId} plans={r.plans} />}
      {r.usage_by_hour && <Hours hours={r.usage_by_hour} />}
      {r.battery && <Battery b={r.battery} />}
      <section>
        <h2>Data quality</h2>
        {r.dq.count === 0 ? <p>No problems found in this file.</p> : <ul>{r.dq.summary.map(s => <li key={s}>{s}</li>)}</ul>}
      </section>
    </>
  )
}

function Plans({ quotes, fileId, plans }: { quotes: Quote[]; fileId: number; plans: Result['plans'] }) {
  const [bill, setBill] = useState<Bill | null>(null)
  const [text, setText] = useState('')
  const best = quotes[0]

  async function check() {
    const res = await fetch(`/api/files/${fileId}/bill`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    })
    setBill(await res.json())
  }

  const diff = bill?.annual_cost_cents != null ? bill.annual_cost_cents - best.annual_cost_cents : null
  return (
    <section>
      <h2>Cheapest plans for your usage</h2>
      <p className="verdict">
        {best.retailer} {best.name} would have cost about <strong>{dollars(best.annual_cost_cents)} a year</strong>.
        {diff !== null && (diff > 0
          ? <> That's <strong>{dollars(diff)}/yr less</strong> than your current plan ({dollars(bill!.annual_cost_cents!)}).</>
          : <> Your current plan ({dollars(bill!.annual_cost_cents!)}) is already about as cheap.</>)}
      </p>
      <table>
        <thead><tr><th>Retailer</th><th>Plan</th><th>Type</th><th>Per year</th></tr></thead>
        <tbody>
          {quotes.map(q => (
            <tr key={q.id}><td>{q.retailer}</td><td>{q.name}</td><td>{q.tariff_type.replace(/_/g, ' ').toLowerCase()}</td><td>{dollars(q.annual_cost_cents)}</td></tr>
          ))}
        </tbody>
      </table>
      <p className="note">
        Out of {plans.total} plans from {plans.retailers} retailers ({plans.source}), {plans.priceable} could be priced.
        Demand and block tariffs are skipped. Prices include GST; controlled load isn't counted.
      </p>
      <h3>Compare with your current bill</h3>
      <textarea rows={6} placeholder="Paste the text of your latest bill" value={text} onChange={e => setText(e.target.value)} />
      <button onClick={check} disabled={!text}>Read my bill</button>
      {bill && (
        <p className="note">
          Read by {bill.extractor === 'llm' ? 'the language model' : 'the regex reader (no LLM key set)'}:{' '}
          {Object.entries(bill.fields).filter(([, v]) => v != null).map(([k, v]) => `${k.replace(/_/g, ' ')} ${v}`).join(', ')}
          {bill.annual_cost_cents == null && '. Not enough to price: we need the usage rate and supply charge.'}
        </p>
      )}
    </section>
  )
}

function Hours({ hours }: { hours: { hour: number; kwh: number }[] }) {
  const max = Math.max(...hours.map(h => h.kwh), 0.001)
  return (
    <section>
      <h2>When you use power</h2>
      <p className="note">Average kWh drawn from the grid in each hour of the day.</p>
      <div className="bars">
        {hours.map(h => (
          <div key={h.hour} title={`${h.hour}:00, ${h.kwh} kWh`} style={{ height: `${(h.kwh / max) * 100}%` }} />
        ))}
      </div>
      <div className="axis"><span>12am</span><span>6am</span><span>12pm</span><span>6pm</span><span>12am</span></div>
    </section>
  )
}

function Battery({ b }: { b: NonNullable<Result['battery']> }) {
  return (
    <section>
      <h2>Would a battery pay off?</h2>
      <p>
        {b.payback_years === null
          ? "You don't export enough solar for a battery to save money on these prices."
          : <>A 10 kWh battery soaking up the solar you now export would save roughly <strong>${b.saving_per_year.toLocaleString("en-AU")} a year</strong>.
            At about ${b.cost.toLocaleString('en-AU')} installed, that's a payback of around <strong>{b.payback_years} years</strong>.</>}
      </p>
      <p className="note">Rough guide only: one battery size, cheapest plan's average rate, no grid charging or VPP payments.</p>
    </section>
  )
}
