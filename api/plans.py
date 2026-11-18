import json
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

from psycopg.types.json import Jsonb

import pricing

BASE = "https://cdr.energymadeeasy.gov.au"
RETAILERS = ["agl", "origin", "energyaustralia", "alinta", "red-energy"]
DISTRIBUTOR = "Ausgrid"
PER_RETAILER = 12
SNAPSHOT = Path(__file__).with_name("plans_snapshot.json")


def get(url, version):
    req = urllib.request.Request(url, headers={"x-v": version, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def trim(plan):
    c = plan.get("electricityContract", {})
    keep = ("pricingModel", "timeZone", "isFixed", "tariffPeriod", "solarFeedInTariff")
    return {
        **{k: plan.get(k) for k in ("planId", "brand", "brandName", "displayName", "type", "customerType", "effectiveFrom")},
        "electricityContract": {k: c[k] for k in keep if k in c},
    }


def fetch_live():
    # list endpoint only speaks x-v 1, the detail endpoint wants 3
    ids = []
    for brand in RETAILERS:
        url = f"{BASE}/{brand}/cds-au/v1/energy/plans?fuelType=ELECTRICITY&effective=CURRENT&page-size=1000"
        found = []
        while url and len(found) < PER_RETAILER:
            page = get(url, "1")
            found += [
                p["planId"] for p in page["data"]["plans"]
                if p.get("customerType") == "RESIDENTIAL" and DISTRIBUTOR in p.get("geography", {}).get("distributors", [])
            ]
            url = page["links"].get("next")
        ids += [(brand, i) for i in sorted(found)[:PER_RETAILER]]
    with ThreadPoolExecutor(8) as pool:
        return list(pool.map(lambda b: trim(get(f"{BASE}/{b[0]}/cds-au/v1/energy/plans/{b[1]}", "3")["data"]), ids))


def save_snapshot(plans):
    SNAPSHOT.write_text(json.dumps({"fetched": date.today().isoformat(), "plans": plans}, separators=(",", ":")))


def load(conn, live=True):
    source = None
    if live:
        try:
            plans, source = fetch_live(), "live"
        except Exception as e:
            print(f"live plan fetch failed ({e}), using snapshot", file=sys.stderr)
    if not source:
        snap = json.loads(SNAPSHOT.read_text())
        plans, source = snap["plans"], f"snapshot {snap['fetched']}"
    with conn.transaction():
        for p in plans:
            reason = pricing.not_priceable(p)
            conn.execute(
                """INSERT INTO plans (plan_id, retailer, name, tariff_type, raw, priceable, reason, source, fetched_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now())
                   ON CONFLICT (plan_id) DO UPDATE SET raw = excluded.raw, priceable = excluded.priceable,
                     reason = excluded.reason, source = excluded.source, fetched_at = now()""",
                (p["planId"], p["brandName"], p["displayName"], p["electricityContract"].get("pricingModel"),
                 Jsonb(p), reason is None, reason, source),
            )
    return source


if __name__ == "__main__":
    plans = fetch_live()
    save_snapshot(plans)
    print(f"{len(plans)} plans -> {SNAPSHOT} ({SNAPSHOT.stat().st_size // 1024} KB)")
