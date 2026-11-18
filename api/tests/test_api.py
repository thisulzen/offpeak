import json
from pathlib import Path

import pytest

import proof

BILL = json.loads((Path(__file__).parent / "fixtures/bills.json").read_text())[0]["text"]


def process(client, fresh, text):
    file_id = client.post("/api/files", content=text.encode()).json()["file_id"]
    proof.run_worker(fresh)
    return client.get(f"/api/files/{file_id}").json()


@pytest.mark.parametrize("defects", [False, True])
def test_upload_to_dashboard(client, fresh, defects):
    text, _ = proof.gen_nem12.generate(defects=defects)
    r = process(client, fresh, text)
    assert r["state"] == "done" and r["nmi"] == "4103000001"
    costs = [q["annual_cost_cents"] for q in r["quotes"]]
    assert len(costs) == 5 and costs == sorted(costs)
    assert len(r["usage_by_hour"]) == 24
    assert (r["dq"]["count"] > 0) == defects
    assert r["plans"]["retailers"] == 5 and r["battery"]["saving_per_year"] > 0


def test_badly_broken_file_is_quarantined_not_priced(client, fresh):
    text, _ = proof.gen_nem12.generate(days=40)
    lines = text.splitlines()
    text = "\n".join(lines[:10] + lines[20:]) + "\n"  # drop 10 of 40 import days mid-file
    r = process(client, fresh, text)
    assert r["state"] == "quarantined" and "quotes" not in r


def test_rejects_non_nem12(client):
    assert client.post("/api/files", content=b"hello").status_code == 400
    assert client.get("/api/files/999").status_code == 404


def test_bill_is_priced_as_the_baseline(client, fresh, monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    text, _ = proof.gen_nem12.generate(days=30)
    file_id = process(client, fresh, text)["file_id"]
    r = client.post(f"/api/files/{file_id}/bill", json={"text": BILL}).json()
    assert r["extractor"] == "regex" and r["fields"]["retailer"] == "AGL"
    assert r["annual_cost_cents"] > 0
