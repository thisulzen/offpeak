"""Data-quality rules for a parsed NEM12 file.

Rules: missing, duplicate, negative, estimated, no_900, interval_mismatch, dst_gap.
Rows with the wrong number of intervals are dropped; negative or blank intervals are skipped;
estimated/substituted reads are kept but flagged. If more than 10% of days are lost the file
is quarantined instead of priced.
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

SYDNEY = ZoneInfo("Australia/Sydney")
QUARANTINE_SHARE = 0.10

SUMMARY = {
    "missing": "{n} day(s) or intervals had no reading",
    "duplicate": "{n} day(s) appeared twice; we kept the first copy",
    "negative": "{n} day(s) had negative readings, which we skipped",
    "estimated": "{n} day(s) were estimated or substituted by the meter provider, not actually read",
    "no_900": "the file has no end-of-file (900) record, so it may be cut short",
    "interval_mismatch": "{n} day(s) had the wrong number of readings and were left out",
    "dst_gap": "{n} day(s) around a daylight-saving change had 23 or 25 hours of readings and were left out",
}


def is_dst_change(d):
    return datetime(d.year, d.month, d.day, 1, tzinfo=SYDNEY).utcoffset() != \
        datetime(d.year, d.month, d.day, 23, tzinfo=SYDNEY).utcoffset()


def check(parsed):
    issues, intervals = [], []
    lost_days = set()

    def flag(rule, row, detail=""):
        issues.append({"rule": rule, "row_ref": f"{row['suffix']} {row['date']:%Y%m%d}", "detail": detail})

    if not (parsed["has_100"] and parsed["has_900"]):
        issues.append({"rule": "no_900", "row_ref": "file", "detail": "missing 100 or 900 record"})

    seen = {}
    for row in parsed["rows"]:
        key = (row["nmi"], row["suffix"], row["date"])
        if key in seen:
            flag("duplicate", row, f"line {row['line']} repeats line {seen[key]}")
            continue
        seen[key] = row["line"]

        expected = 1440 // row["interval"]
        got = len(row["values"])
        if got != expected:
            short = 60 // row["interval"]
            rule = "dst_gap" if is_dst_change(row["date"]) and abs(got - expected) == short else "interval_mismatch"
            flag(rule, row, f"{got} intervals, expected {expected}")
            lost_days.add(row["date"])
            continue

        quality = {row["quality"]} | set(row["interval_quality"].values())
        if quality & {"E", "S", "F"}:
            flag("estimated", row, f"quality {''.join(sorted(quality))}")
        blanks = negatives = 0
        direction = "import" if row["suffix"].startswith("E") else "export" if row["suffix"].startswith("B") else None
        for i, v in enumerate(row["values"]):
            if v is None:
                blanks += 1
            elif v < 0:
                negatives += 1
            elif direction:
                ts = datetime.combine(row["date"], datetime.min.time()) + timedelta(minutes=i * row["interval"])
                intervals.append((row["nmi"], ts, v, direction, row["interval_quality"].get(i, row["quality"])))
        if blanks:
            flag("missing", row, f"{blanks} blank intervals")
        if negatives:
            flag("negative", row, f"{negatives} negative intervals")

    by_stream = {}
    for nmi, suffix, d in seen:
        by_stream.setdefault((nmi, suffix), set()).add(d)
    span = set()
    for (nmi, suffix), days in by_stream.items():
        d, last = min(days), max(days)
        while d <= last:
            span.add(d)
            if d not in days:
                issues.append({"rule": "missing", "row_ref": f"{suffix} {d:%Y%m%d}", "detail": "no 300 record for this day"})
                lost_days.add(d)
            d += timedelta(1)

    quarantine = not intervals or len(lost_days) > QUARANTINE_SHARE * len(span)
    return intervals, issues, quarantine


def summary(issues):
    counts = {}
    for i in issues:
        counts[i["rule"]] = counts.get(i["rule"], 0) + 1
    return [SUMMARY[rule].format(n=n) for rule, n in counts.items()]
