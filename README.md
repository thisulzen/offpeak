# offpeak

Upload your smart-meter file and see what each retail electricity plan would have charged you over that year.

## Why

If you have a smart meter in Australia, you can download your half-hourly usage as a NEM12 file (the AEMO format). Hardly anyone does anything with it. Plans mix flat rates, time-of-use windows, daily supply charges and solar feed-in, so comparing them from a bill is mostly guesswork, and people who never switch tend to pay more. Retailers now have to point you to a "better offer" on your bill, but the retailer decides what gets compared. offpeak runs the numbers on your own interval data.

## What it does

- Takes a NEM12 file, hashes it and queues one job. Uploading the same file again returns the existing result.
- Runs seven data-quality checks: missing days or intervals, duplicate days, negative readings, estimated or substituted reads, a 100 header with no 900 footer, wrong interval counts, and daylight-saving days with 23 or 25 hours of readings. If more than 10% of days are lost, the file is quarantined and not priced.
- Prices every plan it can from the Consumer Data Right (CDR) energy product API: single rate, time-of-use (including seasonal periods and local vs AEST time), daily supply and retailer feed-in, plus 10% GST. Demand and block tariffs are flagged as not priced.
- Reads a pasted bill (rates, supply charge, feed-in, usage, total) and prices it as your current plan, so you get "$X a year less than what you pay now". If `LLM_API_KEY` is set, a Gemini model reads the bill. Without it, a regex reader does. The page tells you which one ran.
- Shows the five cheapest plans, average usage by hour, the data-quality findings in plain words, and a rough payback for a 10 kWh battery that stores solar you'd otherwise export.

## How it's built

`docker compose up` starts four containers:

- `db`: Postgres 16
- `api`: FastAPI with psycopg 3 and raw SQL, no ORM
- `worker`: same image. It claims jobs from a `jobs` table with `FOR UPDATE SKIP LOCKED` (no Redis), moves them through `queued → parsing → validated → priced → done` (or `quarantined` / `failed`), and refreshes plans once a day
- `web`: React + Vite + TypeScript, served by nginx

Each worker step commits in the same transaction as its state change. If a worker dies halfway through, the step rolls back and another worker picks the job up after a 60 second lease.

Plans come live from `cdr.energymadeeasy.gov.au` (the list endpoint takes `x-v: 1`, the plan detail endpoint wants `x-v: 3`). `api/plans_snapshot.json` is a trimmed snapshot taken on 2026-11-18: 12 residential plans each from AGL, Origin, EnergyAustralia, Alinta and Red Energy in the Ausgrid network (Sydney). If the live fetch fails, the worker loads the snapshot. Refresh it with `make plans`.

## Running it

You need Docker. For tests and `make proof` you also need Python 3.12 and Node 22.

```sh
docker compose up --build        # http://localhost:8080
make sample                      # writes samples/clean.csv and samples/defects.csv to upload
```

Set `LLM_API_KEY` (a Gemini API key) in your shell before `docker compose up` to switch on the LLM bill reader. `PLANS_SOURCE=snapshot` skips the live CDR fetch.

```sh
make setup    # venv + npm install
make test     # starts Postgres in Docker, runs pytest and Vitest
make proof    # prints the numbers below
```

## Proof

From `make proof` on my machine (macOS, Postgres 16 in Docker):

| Check | Result |
| --- | --- |
| Data quality | 17/17 planted defects caught across 7 rule types, 0 false positives on the clean file, 0 unexpected issues on the defect file |
| Pricing | 10/10 hand-calculated golden bills matched to the cent (the working is in `api/tests/fixtures/goldens.json`) |
| Idempotent upload | same file uploaded 5 times: 1 file, 1 job, 35,040 intervals before and after |
| Worker kill | worker SIGKILLed mid-parse with rows written but not committed: 0 rows left behind, retried to `done` on attempt 2, 35,040 intervals, all distinct |
| Plans | 60 plans from 5 retailers, 75% priceable, snapshot 2026-11-18 |
| Bill reading, regex | 105/105 fields on 15 templated bills, 27/35 on 5 held-out bills |
| Bill reading, LLM | not measured (no key was set for the run) |
| Tests | 42 pytest, 4 Vitest, all passing |
| Demo file | cheapest plan $1,550/yr, fifth cheapest $1,650/yr; battery saves about $791/yr, payback 11.4 years |

About the bill numbers: I wrote the regex while looking at the 15 templated bills, so 105/105 tells you nothing beyond "it works on what it was built for". The five held-out bills were written afterwards and not tuned against. 27/35 is the honest figure, and it's where an LLM should help. The misses are real: it doubled a usage figure that appeared twice and read "Days in period 92" as 90.

## Azure

`infra/main.bicep` sets up a Container Apps environment (api, worker, web), Postgres Flexible Server on B1ms, and Log Analytics. `.github/workflows/deploy.yml` is manual only. It logs in with OIDC (federated credential, no stored secrets), pushes images to GHCR and deploys the Bicep. The Bicep compiles with `az bicep build`. It has not been deployed: deploy-ready, but not live.

`make proof` also prints a cost estimate from list prices: about US$34 a month, nearly all of it the B1ms database plus one always-on worker replica. The api and web apps scale to zero. Check the Azure pricing calculator before trusting that number.

## Limits

- The meter data is synthetic. `scripts/gen_nem12.py` makes a year of import and solar export for one NMI and can plant defects. I haven't run a real retailer's NEM12 export through it yet.
- Only Ausgrid plans are in the snapshot, and local time is hard-coded to Sydney.
- Controlled load, demand tariffs, block tariffs, time-varying feed-in, discounts and sign-up credits aren't priced. Government premium feed-in schemes are ignored because they're closed to new customers.
- A file covering less than a year is scaled up to 365 days, so seasonal plans get skewed.
- The battery number is a rough guide: one size, an assumed $9,000 installed price, solar-only charging, no VPP payments.
- CI is set up in `.github/workflows/ci.yml` but hasn't run on GitHub yet, so there's no CI timing.
