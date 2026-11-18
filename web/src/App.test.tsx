import { afterEach, expect, test, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import App from './App'
import Results from './Results'

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

const json = (body: unknown, ok = true) => Promise.resolve({ ok, json: () => Promise.resolve(body) })

const done = {
  state: 'done', nmi: '4103000001',
  dq: { summary: ['3 day(s) appeared twice; we kept the first copy'], count: 3 },
  plans: { total: 60, priceable: 45, retailers: 5, source: 'snapshot 2026-11-18' },
  quotes: [
    { id: 1, retailer: 'Alinta Energy', name: 'Home Deal', tariff_type: 'SINGLE_RATE', annual_cost_cents: 152000 },
    { id: 2, retailer: 'AGL', name: 'Value Saver', tariff_type: 'TIME_OF_USE', annual_cost_cents: 161000 },
  ],
  usage_by_hour: Array.from({ length: 24 }, (_, hour) => ({ hour, kwh: 0.5 })),
  battery: { saving_per_year: 610, payback_years: 14.8, cost: 9000 },
}

test('uploading a file shows the cheapest plans', async () => {
  const fetch = vi.fn()
    .mockReturnValueOnce(json({ file_id: 7, state: 'queued', duplicate: false }))
    .mockReturnValueOnce(json(done))
  vi.stubGlobal('fetch', fetch)
  render(<App />)
  fireEvent.change(screen.getByLabelText(/NEM12 file/), { target: { files: [new File(['100,NEM12'], 'meter.csv')] } })
  expect(await screen.findByText(/\$1,520 a year/)).toBeTruthy()
  expect(fetch).toHaveBeenLastCalledWith('/api/files/7')
  expect(screen.getByText(/appeared twice/)).toBeTruthy()
  expect(screen.getByText(/14.8 years/)).toBeTruthy()
})

test('upload errors are shown', async () => {
  vi.stubGlobal('fetch', vi.fn().mockReturnValue(json({ detail: 'no NEM12 interval (300) records found' }, false)))
  render(<App />)
  fireEvent.change(screen.getByLabelText(/NEM12 file/), { target: { files: [new File(['hi'], 'x.csv')] } })
  expect((await screen.findByRole('alert')).textContent).toMatch(/no NEM12/)
})

test('a pasted bill is compared with the cheapest plan', async () => {
  vi.stubGlobal('fetch', vi.fn()
    .mockReturnValueOnce(json(done))
    .mockReturnValueOnce(json({ extractor: 'regex', annual_cost_cents: 190000, fields: { retailer: 'Origin' } })))
  render(<Results fileId={7} onReset={() => {}} />)
  fireEvent.change(await screen.findByPlaceholderText(/Paste/), { target: { value: 'Origin bill' } })
  fireEvent.click(screen.getByText('Read my bill'))
  expect(await screen.findByText(/\$380\/yr less/)).toBeTruthy()
  expect(screen.getByText(/regex reader/)).toBeTruthy()
})

test('a quarantined file is explained, not priced', async () => {
  vi.stubGlobal('fetch', vi.fn().mockReturnValue(json({ ...done, state: 'quarantined', quotes: undefined, usage_by_hour: undefined, battery: undefined })))
  render(<Results fileId={7} onReset={() => {}} />)
  expect((await screen.findByRole('alert')).textContent).toMatch(/too many gaps/)
  expect(screen.queryByText(/a year/)).toBeNull()
})
