import json
from pathlib import Path

import extract

BILLS = json.loads((Path(__file__).parent / "fixtures/bills.json").read_text())


def test_regex_reads_every_templated_bill():
    for b in BILLS:
        if not b.get("held_out"):
            assert extract.regex(b["text"]).model_dump() == b["expected"]


def test_dollar_rates_become_cents():
    b = extract.regex("Anytime energy 100 kWh x $0.3125 per kWh\nDaily supply charge $1.10 per day")
    assert (b.usage_rate_c, b.supply_c_per_day, b.usage_kwh) == (31.25, 110.0, 100.0)


def test_off_peak_rate_is_not_the_usage_rate():
    assert extract.regex("Off-peak 20 c/kWh\nPeak 40 c/kWh").usage_rate_c == 40.0


def test_without_key_regex_runs(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    assert extract.extract("AGL 30 days")[1] == "regex"


def test_llm_failure_falls_back_to_regex(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "x")
    monkeypatch.setattr(extract, "llm", lambda text: 1 / 0)
    bill, how = extract.extract("Origin 30 days")
    assert how == "regex" and bill.retailer == "Origin"
