"""Job worker. Polls the jobs table with FOR UPDATE SKIP LOCKED, so any number can run.

queued -> parsing -> validated -> priced -> done, or quarantined / failed.
Each step commits together with its state change, so a crash mid-step rolls the
step back and the next worker to claim the job (after LEASE seconds) redoes it.

    python worker.py           run forever
    python worker.py --drain   exit once the queue is empty
"""
import os
import sys
import time
import traceback

from psycopg.types.json import Jsonb

import db
import dq
import nem12
import plans
import pricing

LEASE = int(os.environ.get("LEASE_SECONDS", "60"))
MAX_ATTEMPTS = 3
PAUSE = float(os.environ.get("WORKER_TEST_PAUSE", "0"))  # lets the kill test catch a parse in flight

NEXT = {
    "queued": {"parsing"},
    "parsing": {"validated", "quarantined"},
    "validated": {"priced"},
    "priced": {"done"},
}


def can_move(src, dst):
    return dst in NEXT.get(src, set()) or (dst == "failed" and src in NEXT)


def move(conn, job, dst):
    if not can_move(job["state"], dst):
        raise ValueError(f"job {job['id']}: {job['state']} -> {dst} not allowed")
    cur = conn.execute(
        "UPDATE jobs SET state = %s, updated_at = now() WHERE id = %s AND state = %s",
        (dst, job["id"], job["state"]),
    )
    if cur.rowcount != 1:
        raise RuntimeError(f"job {job['id']} changed under us")
    job["state"] = dst


def claim(conn):
    return conn.execute(
        """UPDATE jobs SET locked_at = now(), attempts = attempts + 1, updated_at = now()
           WHERE id = (
             SELECT id FROM jobs
             WHERE state NOT IN ('done', 'failed', 'quarantined')
               AND (locked_at IS NULL OR locked_at < now() - make_interval(secs => %s))
             ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 1)
           RETURNING *""",
        (LEASE,),
    ).fetchone()


def parse_step(conn, job):
    body = conn.execute("SELECT body FROM meter_files WHERE id = %s", (job["file_id"],)).fetchone()["body"]
    parsed = nem12.parse(body)
    intervals, issues, quarantine = dq.check(parsed)
    with conn.transaction():
        conn.execute("DELETE FROM dq_issues WHERE file_id = %s", (job["file_id"],))
        conn.execute("DELETE FROM intervals WHERE file_id = %s", (job["file_id"],))
        conn.execute("UPDATE meter_files SET nmi = %s WHERE id = %s", (parsed["nmi"], job["file_id"]))
        with conn.cursor().copy("COPY intervals (file_id, nmi, ts, kwh, direction, quality) FROM STDIN") as cp:
            for row in intervals:
                cp.write_row((job["file_id"], *row))
        with conn.cursor().copy("COPY dq_issues (file_id, rule, row_ref, detail) FROM STDIN") as cp:
            for i in issues:
                cp.write_row((job["file_id"], i["rule"], i["row_ref"], i["detail"]))
        if PAUSE:
            time.sleep(PAUSE)
        move(conn, job, "quarantined" if quarantine else "validated")


def load_intervals(conn, file_id):
    return [
        (r["ts"], float(r["kwh"]), r["direction"])
        for r in conn.execute("SELECT ts, kwh, direction FROM intervals WHERE file_id = %s", (file_id,))
    ]


def price_step(conn, job):
    intervals = load_intervals(conn, job["file_id"])
    rows = conn.execute("SELECT id, raw FROM plans WHERE priceable").fetchall()
    with conn.transaction():
        conn.execute("DELETE FROM quotes WHERE file_id = %s", (job["file_id"],))
        for p in rows:
            q = pricing.price(pricing.normalise(p["raw"]), intervals)
            conn.execute(
                "INSERT INTO quotes (file_id, plan_id, annual_cost_cents, breakdown) VALUES (%s, %s, %s, %s)",
                (job["file_id"], p["id"], pricing.annual_cents(q), Jsonb(q)),
            )
        move(conn, job, "priced")


def run(conn, job):
    if job["attempts"] > MAX_ATTEMPTS:
        move(conn, job, "failed")
        conn.execute("UPDATE jobs SET locked_at = NULL WHERE id = %s", (job["id"],))
        return
    try:
        if job["state"] == "queued":
            move(conn, job, "parsing")
        if job["state"] == "parsing":
            parse_step(conn, job)
        if job["state"] == "validated":
            price_step(conn, job)
        if job["state"] == "priced":
            move(conn, job, "done")
        conn.execute("UPDATE jobs SET locked_at = NULL WHERE id = %s", (job["id"],))
    except Exception:
        err = traceback.format_exc(limit=3)
        print(err, file=sys.stderr)
        # release the lease so another attempt can start straight away
        conn.execute("UPDATE jobs SET error = %s, locked_at = NULL WHERE id = %s", (err, job["id"]))


def refresh_plans(conn):
    stale = conn.execute("SELECT coalesce(max(fetched_at) < now() - interval '1 day', true) AS s FROM plans").fetchone()["s"]
    if stale:
        with conn.transaction():
            if conn.execute("SELECT pg_try_advisory_xact_lock(7) AS ok").fetchone()["ok"]:
                print("plans:", plans.load(conn, live=os.environ.get("PLANS_SOURCE") != "snapshot"))


def main(drain=False):
    conn = db.connect()
    db.migrate(conn)
    while True:
        refresh_plans(conn)
        job = claim(conn)
        if job:
            run(conn, job)
        elif drain:
            return
        else:
            time.sleep(1)


if __name__ == "__main__":
    main(drain="--drain" in sys.argv)
