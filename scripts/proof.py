"""Measures the numbers quoted in the README. Needs Postgres (make db).

    python scripts/proof.py
"""
import json
import os
import re
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API = ROOT / "api"
sys.path[:0] = [str(API), str(ROOT / "scripts")]

import psycopg  # noqa: E402

import gen_nem12  # noqa: E402

ADMIN_URL = os.environ.get("DATABASE_URL", "postgresql://offpeak:offpeak@localhost:5432/offpeak")


def fresh_db(name):
    """Recreate an empty database next to the dev one, load the plan snapshot, return its URL."""
    with psycopg.connect(ADMIN_URL, autocommit=True) as c:
        c.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
        c.execute(f"CREATE DATABASE {name}")
    url = ADMIN_URL.rsplit("/", 1)[0] + "/" + name
    import db
    import plans

    with db.connect(url) as conn:
        db.migrate(conn)
        plans.load(conn, live=False)
    return url


def upload(url, text):
    os.environ["DATABASE_URL"] = url
    import db

    db.URL = url
    from fastapi.testclient import TestClient

    import app

    with TestClient(app.app) as client:
        r = client.post("/api/files", content=text.encode())
        r.raise_for_status()
        return r.json()


def run_worker(url, pause=0, wait=True):
    env = {**os.environ, "DATABASE_URL": url, "PLANS_SOURCE": "snapshot", "LEASE_SECONDS": "1", "WORKER_TEST_PAUSE": str(pause)}
    p = subprocess.Popen([sys.executable, "worker.py", "--drain"], cwd=API, env=env)
    if wait:
        p.wait(60)
    return p


def counts(url, file_id):
    with psycopg.connect(url) as c:
        return c.execute(
            """SELECT (SELECT count(*) FROM meter_files) AS files,
                      (SELECT count(*) FROM jobs) AS jobs,
                      (SELECT state FROM jobs WHERE file_id = %(f)s) AS state,
                      (SELECT attempts FROM jobs WHERE file_id = %(f)s) AS attempts,
                      (SELECT count(*) FROM intervals WHERE file_id = %(f)s) AS intervals,
                      (SELECT count(DISTINCT (nmi, ts, direction)) FROM intervals WHERE file_id = %(f)s) AS distinct_intervals""",
            {"f": file_id},
        ).fetchone()


def idempotency(url, times=5):
    text, _ = gen_nem12.generate(seed=3)
    first = upload(url, text)
    run_worker(url)
    before = counts(url, first["file_id"])
    again = [upload(url, text) for _ in range(times - 1)]
    run_worker(url)
    after = counts(url, first["file_id"])
    return {"uploads": times, "files": after[0], "jobs": after[1], "rows_before": before[4], "rows_after": after[4],
            "same_id": all(a["file_id"] == first["file_id"] and a["duplicate"] for a in again)}


def kill_retry(url):
    text, _ = gen_nem12.generate(seed=4)
    file_id = upload(url, text)["file_id"]
    victim = run_worker(url, pause=30, wait=False)
    # wait until the worker sits inside its parse transaction with rows written but not committed
    with psycopg.connect(url, autocommit=True) as c:
        for _ in range(200):
            busy = c.execute(
                "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() AND state = 'idle in transaction'"
            ).fetchone()[0]
            if busy:
                break
            time.sleep(0.05)
    victim.send_signal(signal.SIGKILL)
    victim.wait()
    mid = counts(url, file_id)
    time.sleep(1.2)  # let the lease lapse
    run_worker(url)
    end = counts(url, file_id)
    return {"state_after_kill": mid[2], "rows_after_kill": mid[4], "state": end[2], "attempts": end[3],
            "rows": end[4], "distinct": end[5]}


def dq_score():
    import dq
    import nem12

    clean, _ = gen_nem12.generate()
    broken, planted = gen_nem12.generate(defects=True)
    found = {(i["rule"], i["row_ref"]) for i in dq.check(nem12.parse(broken))[1]}
    clean_issues = dq.check(nem12.parse(clean))[1]
    return {"planted": len(planted), "types": len({r for r, _ in planted}), "caught": len(set(planted) & found),
            "extra": len(found - set(planted)), "clean_false_positives": len(clean_issues)}


def goldens():
    import pricing

    cases = json.loads((API / "tests/fixtures/goldens.json").read_text())
    ok = sum(
        pricing.price(pricing.normalise(g["plan"]), [(datetime.fromisoformat(t), k, d) for t, k, d in g["intervals"]])["cents"] == g["cents"]
        for g in cases
    )
    return ok, len(cases)


def extraction():
    import extract

    bills = json.loads((API / "tests/fixtures/bills.json").read_text())
    fields = list(extract.Bill.model_fields)

    def score(fn, subset):
        hits = sum(getattr(fn(b["text"]), f) == b["expected"][f] for b in subset for f in fields)
        return f"{hits}/{len(subset) * len(fields)}"

    out = {}
    for label, subset in (("templated", [b for b in bills if not b.get("held_out")]), ("held_out", [b for b in bills if b.get("held_out")])):
        out[f"regex_{label}"] = score(extract.regex, subset)
        out[f"llm_{label}"] = score(extract.llm, subset) if os.environ.get("LLM_API_KEY") else "skipped (no LLM_API_KEY)"
    return out


def plan_stats(url):
    with psycopg.connect(url) as c:
        return c.execute(
            "SELECT count(DISTINCT retailer), count(*), round(100.0 * count(*) FILTER (WHERE priceable) / count(*)), max(source) FROM plans"
        ).fetchone()


def demo(url):
    text, _ = gen_nem12.generate(defects=True)
    file_id = upload(url, text)["file_id"]
    run_worker(url)
    from fastapi.testclient import TestClient

    import app

    with TestClient(app.app) as client:
        r = client.get(f"/api/files/{file_id}").json()
    return r


def test_counts():
    py = subprocess.run([sys.executable, "-m", "pytest", "-q"], cwd=API, capture_output=True, text=True).stdout
    js = subprocess.run(["npx", "vitest", "run"], cwd=ROOT / "web", capture_output=True, text=True)
    js_out = re.sub(r"\x1b\[[0-9;]*m", "", js.stdout + js.stderr)
    return (re.findall(r"(\d+ passed[^\n]*)", py) or ["pytest output not understood"])[-1], \
        (re.findall(r"Tests\s+(\d+ passed[^\n]*)", js_out) or ["vitest did not run"])[-1]


def azure_estimate():
    # USD list prices, Australia East, as of late 2026; check the pricing calculator before relying on them
    hours = 730
    postgres = 0.0207 * hours + 32 * 0.13  # B1ms compute + 32 GB storage
    worker = max(0, 0.25 * hours * 3600 - 180_000) * 0.000024 + max(0, 0.5 * hours * 3600 - 360_000) * 0.000003
    return round(postgres + worker)  # api and web scale to zero and fit in the free grant at demo traffic


def main():
    url = fresh_db("offpeak_proof")
    s = dq_score()
    print(f"data quality: {s['caught']}/{s['planted']} planted defects caught across {s['types']} rule types, "
          f"{s['extra']} unexpected issues on the defect file, {s['clean_false_positives']} false positives on the clean file")
    ok, n = goldens()
    print(f"golden bills: {ok}/{n} match the hand calculation to the cent")
    i = idempotency(url)
    print(f"idempotency: same file uploaded {i['uploads']}x -> {i['files']} file, {i['jobs']} job, "
          f"intervals {i['rows_before']} -> {i['rows_after']}, same id returned: {i['same_id']}")
    k = kill_retry(url)
    print(f"kill test: worker SIGKILLed mid-parse (job '{k['state_after_kill']}', {k['rows_after_kill']} rows committed), "
          f"retried -> '{k['state']}' after {k['attempts']} attempts, {k['rows']} intervals, {k['distinct']} distinct")
    r, total, pct, source = plan_stats(url)
    print(f"plans: {total} plans from {r} retailers, {pct}% priceable, {source}")
    print("bill extraction (field accuracy):", ", ".join(f"{k} {v}" for k, v in extraction().items()))
    d = demo(url)
    q = d["quotes"]
    print(f"demo file: cheapest plan ${q[0]['annual_cost_cents'] / 100:,.0f}/yr ({q[0]['retailer']} {q[0]['name']}), "
          f"5th cheapest ${q[-1]['annual_cost_cents'] / 100:,.0f}/yr; battery saving ${d['battery']['saving_per_year']}/yr, "
          f"payback {d['battery']['payback_years']} years")
    py, js = test_counts()
    print(f"tests: pytest {py}; vitest {js}")
    print(f"azure estimate: about US${azure_estimate()}/month (Postgres B1ms + one always-on worker replica)")


if __name__ == "__main__":
    main()
