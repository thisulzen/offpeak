"""Prices a CDR electricity plan against interval data.

CDR prices are dollars excluding GST. We add 10% GST to usage and supply;
feed-in credits carry no GST. Controlled load is ignored.
"""
from datetime import date, timedelta, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo

DAYS = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]
AEST = timezone(timedelta(hours=10))
# ponytail: one zone because the snapshot is Ausgrid only; key it off the distributor if more states come in
LOCAL = ZoneInfo("Australia/Sydney")
GST = 1.1
BATTERY_COST = 9000  # rough installed price of a ~10 kWh home battery after the federal rebate
BATTERY_KWH = 10


class NotPriceable(Exception):
    pass


def minutes(hhmm):
    h, m = hhmm.split(":")[:2]
    return int(h) * 60 + int(m)


def in_window(m, start, end):
    return start <= m < end if start < end else m >= start or m < end


def flat(rates):
    # some retailers publish two "blocks" at the same price; that's still a flat rate
    prices = {float(r["unitPrice"]) for r in rates}
    if len(prices) != 1:
        raise NotPriceable("block tariff")
    return prices.pop()


def week_table(tou_rates):
    table = [[None] * 48 for _ in range(7)]
    for r in tou_rates:
        price = flat(r.get("rates", []))
        for w in r.get("timeOfUse", []):
            start, end = minutes(w["startTime"]), minutes(w["endTime"])
            if end % 30 == 29:  # "09:59" means up to 10:00
                end = (end + 1) % 1440
            for d in w.get("days") or DAYS:
                if d not in DAYS:
                    continue
                row = table[DAYS.index(d)]
                for s in range(48):
                    if row[s] is None and in_window(s * 30, start, end):
                        row[s] = price
    if any(None in row for row in table):
        raise NotPriceable("time-of-use windows leave gaps")
    return table


def in_season(md, start, end):
    return start <= md <= end if start <= end else md >= start or md <= end


def normalise(raw):
    c = raw.get("electricityContract") or {}
    periods = []
    for tp in c.get("tariffPeriod") or []:
        kind = tp.get("rateBlockUType")
        if kind == "demandCharges" or tp.get("demandCharges"):
            raise NotPriceable("demand tariff")
        if tp.get("dailySupplyChargeType", "SINGLE") != "SINGLE":
            raise NotPriceable("banded supply charge")
        if kind == "singleRate":
            table = [[flat(tp["singleRate"]["rates"])] * 48 for _ in range(7)]
        elif kind == "timeOfUseRates":
            table = week_table(tp["timeOfUseRates"])
        else:
            raise NotPriceable(f"unsupported rate type {kind}")
        periods.append((tp.get("startDate", "01-01"), tp.get("endDate", "12-31"), table, float(tp.get("dailySupplyCharge") or 0)))
    if not periods:
        raise NotPriceable("no tariff periods")

    by_md = {}
    for n in range(366):
        d = date(2024, 1, 1) + timedelta(n)
        hit = next((i for i, p in enumerate(periods) if in_season(f"{d:%m-%d}", p[0], p[1])), None)
        if hit is None:
            raise NotPriceable("tariff periods leave dates uncovered")
        by_md[(d.month, d.day)] = hit

    feed_in = 0.0
    for f in c.get("solarFeedInTariff") or []:
        if f.get("payerType") == "RETAILER" and f.get("tariffUType") == "singleTariff":
            feed_in = float(f["singleTariff"]["rates"][0]["unitPrice"])
            break
    return {"periods": periods, "by_md": by_md, "feed_in": feed_in, "local": c.get("timeZone") == "LOCAL"}


def not_priceable(raw):
    try:
        normalise(raw)
    except NotPriceable as e:
        return str(e)
    except (KeyError, ValueError, TypeError, IndexError) as e:
        return f"malformed plan ({e!r})"


@lru_cache(maxsize=None)
def to_local(ts):
    return ts.replace(tzinfo=AEST).astimezone(LOCAL).replace(tzinfo=None)


def price(plan, intervals):
    """intervals: (ts, kwh, direction), ts is the naive interval start in NEM time (AEST)."""
    usage = feed = 0.0
    days = {}
    for ts, kwh, direction in intervals:
        days.setdefault(ts.date(), plan["by_md"][(ts.month, ts.day)])
        if direction == "export":
            feed += kwh * plan["feed_in"]
            continue
        t = to_local(ts) if plan["local"] else ts
        table = plan["periods"][plan["by_md"][(t.month, t.day)]][2]
        usage += kwh * table[t.weekday()][t.hour * 2 + t.minute // 30]
    supply = sum(plan["periods"][i][3] for i in days.values())
    return {
        "days": len(days),
        "usage": round(usage * GST, 2),
        "supply": round(supply * GST, 2),
        "feed_in": round(feed, 2),
        "cents": round(((usage + supply) * GST - feed) * 100),
    }


def annual_cents(quote):
    return round(quote["cents"] * 365 / quote["days"]) if quote["days"] else 0


def battery(intervals, import_rate, feed_in_rate):
    """Rough payback: a battery that only stores solar you'd otherwise export.

    Rates in $/kWh incl GST. Returns yearly saving and payback years (None if it never pays back).
    """
    soc = stored = shifted = 0.0
    days = set()
    for ts, kwh, direction in sorted(intervals):
        days.add(ts.date())
        if direction == "export":
            take = min(kwh, BATTERY_KWH - soc)
            soc += take
            stored += take
        else:
            out = min(kwh, soc)
            soc -= out
            shifted += out * 0.9  # round-trip losses
    if not days:
        return {"saving_per_year": 0, "payback_years": None}
    saving = (shifted * import_rate - stored * feed_in_rate) * 365 / len(days)
    return {
        "saving_per_year": round(saving),
        "payback_years": round(BATTERY_COST / saving, 1) if saving > 0 else None,
        "cost": BATTERY_COST,
    }
