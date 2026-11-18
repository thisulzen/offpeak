import hashlib
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

import db
import dq
import extract
import pricing
from worker import load_intervals

MAX_UPLOAD = 20 * 1024 * 1024


@asynccontextmanager
async def lifespan(app):
    with db.connect() as conn:
        db.migrate(conn)
    yield


app = FastAPI(title="offpeak", lifespan=lifespan)


@app.get("/api/health")
def health():
    return {"ok": True}


@app.post("/api/files")
async def upload(request: Request):
    body = await request.body()
    if len(body) > MAX_UPLOAD:
        raise HTTPException(413, "file too large")
    try:
        text = body.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(400, "not a text file") from None
    if "\n300," not in text:
        raise HTTPException(400, "no NEM12 interval (300) records found")
    sha = hashlib.sha256(body).hexdigest()
    with db.connect() as conn, conn.transaction():
        row = conn.execute(
            "INSERT INTO meter_files (sha256, body) VALUES (%s, %s) ON CONFLICT (sha256) DO NOTHING RETURNING id",
            (sha, text),
        ).fetchone()
        duplicate = row is None
        if duplicate:
            row = conn.execute("SELECT id FROM meter_files WHERE sha256 = %s", (sha,)).fetchone()
        else:
            conn.execute("INSERT INTO jobs (file_id) VALUES (%s)", (row["id"],))
    return {"file_id": row["id"], "duplicate": duplicate, **file_state(row["id"])}


def file_state(file_id):
    with db.connect() as conn:
        job = conn.execute(
            "SELECT j.state, j.attempts, f.nmi FROM jobs j JOIN meter_files f ON f.id = j.file_id WHERE j.file_id = %s",
            (file_id,),
        ).fetchone()
    if not job:
        raise HTTPException(404, "no such file")
    return dict(job)


@app.get("/api/files/{file_id}")
def result(file_id: int):
    out = {"file_id": file_id, **file_state(file_id)}
    with db.connect() as conn:
        issues = conn.execute("SELECT rule, row_ref, detail FROM dq_issues WHERE file_id = %s ORDER BY row_ref", (file_id,)).fetchall()
        out["dq"] = {"summary": dq.summary(issues), "issues": issues[:100], "count": len(issues)}
        out["plans"] = conn.execute(
            """SELECT count(*) AS total, count(*) FILTER (WHERE priceable) AS priceable,
                      count(DISTINCT retailer) AS retailers, max(source) AS source FROM plans"""
        ).fetchone()
        if out["state"] != "done":
            return out
        out["quotes"] = conn.execute(
            # retailers publish near-identical variants under one name; show each name once
            """SELECT * FROM (
                 SELECT DISTINCT ON (p.retailer, p.name) p.id, p.retailer, p.name, p.tariff_type, p.raw, q.annual_cost_cents, q.breakdown
                 FROM quotes q JOIN plans p ON p.id = q.plan_id
                 WHERE q.file_id = %s ORDER BY p.retailer, p.name, q.annual_cost_cents) best
               ORDER BY annual_cost_cents LIMIT 5""",
            (file_id,),
        ).fetchall()
        out["usage_by_hour"] = conn.execute(
            """SELECT extract(hour FROM ts)::int AS hour, round(sum(kwh) / count(DISTINCT ts::date), 3)::float AS kwh
               FROM intervals WHERE file_id = %s AND direction = 'import' GROUP BY 1 ORDER BY 1""",
            (file_id,),
        ).fetchall()
        intervals = load_intervals(conn, file_id)

    if out["quotes"]:
        best = out["quotes"][0]
        imported = sum(k for _, k, d in intervals if d == "import")
        rate = best["breakdown"]["usage"] / imported if imported else 0
        out["battery"] = pricing.battery(intervals, rate, pricing.normalise(best["raw"])["feed_in"])
    for q in out["quotes"]:
        del q["raw"]
    return out


class BillText(BaseModel):
    text: str


@app.post("/api/files/{file_id}/bill")
def bill(file_id: int, body: BillText):
    fields, how = extract.extract(body.text[:20000])
    out = {"fields": fields.model_dump(), "extractor": how, "annual_cost_cents": None}
    if fields.usage_rate_c is None or fields.supply_c_per_day is None:
        return out
    # bill rates include GST; CDR-style plans don't
    plan = pricing.normalise({"electricityContract": {
        "tariffPeriod": [{
            "rateBlockUType": "singleRate",
            "singleRate": {"rates": [{"unitPrice": fields.usage_rate_c / 110}]},
            "dailySupplyCharge": fields.supply_c_per_day / 110,
        }],
        "solarFeedInTariff": [{
            "payerType": "RETAILER", "tariffUType": "singleTariff",
            "singleTariff": {"rates": [{"unitPrice": (fields.feed_in_c or 0) / 100}]},
        }],
    }})
    with db.connect() as conn:
        intervals = load_intervals(conn, file_id)
    if intervals:
        out["annual_cost_cents"] = pricing.annual_cents(pricing.price(plan, intervals))
    return out
