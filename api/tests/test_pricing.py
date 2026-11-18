import json
from datetime import datetime
from pathlib import Path

import pytest

import plans
import pricing

GOLDENS = json.loads((Path(__file__).parent / "fixtures/goldens.json").read_text())


@pytest.mark.parametrize("g", GOLDENS, ids=[g["name"] for g in GOLDENS])
def test_golden_bills(g):
    intervals = [(datetime.fromisoformat(t), k, d) for t, k, d in g["intervals"]]
    assert pricing.price(pricing.normalise(g["plan"]), intervals)["cents"] == g["cents"], g["working"]


def tariff(**tp):
    return {"electricityContract": {"tariffPeriod": [tp]}}


@pytest.mark.parametrize("raw, reason", [
    (tariff(rateBlockUType="demandCharges"), "demand tariff"),
    (tariff(rateBlockUType="singleRate", singleRate={"rates": [{"unitPrice": "0.2", "volume": 10}, {"unitPrice": "0.3"}]}), "block tariff"),
    (tariff(rateBlockUType="timeOfUseRates", timeOfUseRates=[{"rates": [{"unitPrice": "0.5"}], "timeOfUse": [{"startTime": "16:00", "endTime": "20:59"}]}]),
     "time-of-use windows leave gaps"),
    (tariff(rateBlockUType="singleRate", singleRate={"rates": [{"unitPrice": "0.2"}]}, startDate="01-01", endDate="06-30"),
     "tariff periods leave dates uncovered"),
])
def test_unpriceable_plans_say_why(raw, reason):
    assert pricing.not_priceable(raw) == reason


def test_snapshot_mostly_priceable():
    snap = json.loads(plans.SNAPSHOT.read_text())
    ok = [p for p in snap["plans"] if pricing.not_priceable(p) is None]
    assert len({p["brand"] for p in snap["plans"]}) == 5
    assert len(ok) / len(snap["plans"]) > 0.5


def test_battery_only_counts_stored_solar():
    day = [(datetime(2026, 1, 5, 12), 5.0, "export"), (datetime(2026, 1, 5, 19), 4.0, "import")]
    b = pricing.battery(day, import_rate=0.40, feed_in_rate=0.05)
    # stores 5, discharges 4 x 0.9 = 3.6 kWh: 3.6 x 0.40 - 5 x 0.05 = 1.19 a day
    assert b["saving_per_year"] == round(1.19 * 365)
    assert pricing.battery([(datetime(2026, 1, 5, 19), 4.0, "import")], 0.4, 0.05)["payback_years"] is None
