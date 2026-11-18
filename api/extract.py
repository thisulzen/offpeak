"""Pull the numbers that matter out of pasted electricity-bill text.

If LLM_API_KEY is set, a Gemini model reads the bill; otherwise (or if that call fails)
a regex extractor does. Both return the same Bill.
"""
import json
import os
import re
import urllib.request

from pydantic import BaseModel


class Bill(BaseModel):
    retailer: str | None = None
    billing_days: int | None = None
    usage_kwh: float | None = None
    usage_rate_c: float | None = None  # c/kWh incl GST
    supply_c_per_day: float | None = None  # incl GST
    feed_in_c: float | None = None
    total_dollars: float | None = None


RETAILERS = ["AGL", "Origin", "EnergyAustralia", "Alinta", "Red Energy", "Powershop", "Simply Energy", "Momentum", "Lumo", "Dodo"]


RATE = re.compile(r"(\$)?\s*(\d[\d,]*\.?\d*)\s*(c|cents|¢)?\s*(?:/|per)\s*(kwh|day)", re.I)
QTY = re.compile(r"(\d[\d,]*\.?\d*)\s*kwh\b(?!\s*(?:a|per) day)", re.I)
TOTAL = re.compile(r"(?:total amount due|amount due|total due|please pay|new charges|total)\s*:?\s*\$?\s*(\d[\d,]*\.\d\d)", re.I)


def num(s):
    return float(s.replace(",", ""))


def regex(text):
    b = Bill()
    low = text.lower()
    b.retailer = next((r for r in RETAILERS if r.lower() in low), None)
    if m := re.search(r"(\d+)[\s-]*days?\b", text, re.I):
        b.billing_days = int(m[1])
    if m := TOTAL.search(text):
        b.total_dollars = num(m[1])

    used = []
    for line in text.splitlines():
        solar = re.search(r"solar|feed|export", line, re.I)
        if not solar and (m := QTY.search(line)) and not RATE.match(line, m.start()):
            used.append(num(m[1]))
        last = 0
        for m in RATE.finditer(line):
            label = line[last:m.start()].lower()
            last = m.end()
            value = num(m[2]) * 100 if m[1] else num(m[2])
            if m[4].lower() == "day":
                b.supply_c_per_day = b.supply_c_per_day or round(value, 4)
            elif re.search(r"solar|feed|export", label) or (solar and not label.strip(" .:x@")):
                b.feed_in_c = b.feed_in_c or round(value, 4)
            elif "off" not in label:
                b.usage_rate_c = b.usage_rate_c or round(value, 4)
    if used:
        b.usage_kwh = sum(used)
    return b


def llm(text):
    model = os.environ.get("LLM_MODEL", "gemini-2.5-flash")
    prompt = (
        "Extract these fields from the electricity bill below and reply with JSON only. "
        "Use null when a field is not on the bill. Rates are in cents including GST. "
        f"Fields: {json.dumps(Bill.model_json_schema()['properties'])}\n\nBill:\n{text}"
    )
    req = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        data=json.dumps({
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0},
        }).encode(),
        headers={"Content-Type": "application/json", "x-goog-api-key": os.environ["LLM_API_KEY"]},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        reply = json.load(r)["candidates"][0]["content"]["parts"][0]["text"]
    return Bill.model_validate_json(reply)


def extract(text):
    if os.environ.get("LLM_API_KEY"):
        try:
            return llm(text), "llm"
        except Exception as e:
            print(f"LLM extraction failed, falling back to regex: {e!r}")
    return regex(text), "regex"
