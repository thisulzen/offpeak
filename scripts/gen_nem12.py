"""Synthetic NEM12 files: a year of half-hourly import (E1) and solar export (B1).

With defects=True it plants known problems and returns a manifest of them,
so the data-quality checks can be scored.

    python scripts/gen_nem12.py out.csv [--defects] [--seed N]
"""
import math
import random
import sys
from datetime import date, timedelta

NMI = "4103000001"
START = date(2025, 7, 1)
DST_DAYS = [date(2025, 10, 5), date(2026, 4, 5)]  # NSW clock changes inside the default year


def profile(day, slot, rng, solar_kw):
    h = slot / 2
    winter = math.cos((day.timetuple().tm_yday - 196) / 365 * 2 * math.pi)  # 1 in mid July
    load = 0.12 + 0.05 * winter
    if 6.5 <= h < 9:
        load += 0.35
    if 17 <= h < 22.5:
        load += 0.55 + 0.25 * abs(winter)
    load *= rng.uniform(0.7, 1.3)
    sun = max(0.0, math.sin((h - 6.5) / 11 * math.pi)) if 6.5 <= h <= 17.5 else 0.0
    solar = solar_kw * 0.5 * sun * (0.75 - 0.25 * winter) * rng.uniform(0.4, 1.0)
    return max(load - solar, 0), max(solar - load, 0)


def generate(days=365, seed=1, defects=False, solar_kw=5.0, start=START):
    rng = random.Random(seed)
    dates = [start + timedelta(n) for n in range(days)]
    rows = {"E1": {}, "B1": {}}
    for d in dates:
        pairs = [profile(d, s, rng, solar_kw) for s in range(48)]
        rows["E1"][d] = {"values": [f"{a:.3f}" for a, _ in pairs], "quality": "A"}
        rows["B1"][d] = {"values": [f"{b:.3f}" for _, b in pairs], "quality": "A"}

    planted = []
    extra = []  # duplicated rows, written after the originals
    footer = True
    if defects:
        normal = [d for d in dates if d not in DST_DAYS]
        picks = iter(rng.sample(normal, 16))

        def plant(rule, d, suffix="E1"):
            planted.append((rule, f"{suffix} {d:%Y%m%d}"))

        for _ in range(3):
            d = next(picks)
            del rows["E1"][d]
            plant("missing", d)
        for _ in range(3):
            d = next(picks)
            extra.append(("E1", d, dict(rows["E1"][d])))
            plant("duplicate", d)
        for _ in range(3):
            d = next(picks)
            rows["E1"][d]["values"][rng.randrange(48)] = "-0.250"
            plant("negative", d)
        for q in ("E", "S", "E"):
            d = next(picks)
            rows["E1"][d]["quality"] = q
            plant("estimated", d)
        for _ in range(2):
            d = next(picks)
            rows["E1"][d]["values"] = rows["E1"][d]["values"][:47]
            plant("interval_mismatch", d)
        for d in DST_DAYS:
            if d in rows["E1"]:
                v = rows["E1"][d]["values"]
                rows["E1"][d]["values"] = v[:46] if d.month == 10 else v + v[-2:]
                plant("dst_gap", d)
        footer = False
        planted.append(("no_900", "file"))

    out = ["100,NEM12,202607010300,OFFPEAKGEN,OFFPEAK"]
    for suffix, stream in (("E1", "N1"), ("B1", "N2")):
        out.append(f"200,{NMI},E1B1,{suffix},{suffix},{stream},MTR0001,KWH,30,")
        for d in sorted(rows[suffix]):
            r = rows[suffix][d]
            out.append(row_300(d, r))
        out += [row_300(d, r) for s, d, r in extra if s == suffix]
    if footer:
        out.append("900")
    return "\n".join(out) + "\n", planted


def row_300(d, r):
    reason = "51" if r["quality"] != "A" else ""
    return f"300,{d:%Y%m%d},{','.join(r['values'])},{r['quality']},{reason},,{d + timedelta(1):%Y%m%d}000000,"


if __name__ == "__main__":
    args = sys.argv[1:]
    seed = int(args[args.index("--seed") + 1]) if "--seed" in args else 1
    text, planted = generate(seed=seed, defects="--defects" in args)
    open(args[0], "w").write(text)
    print(f"wrote {args[0]}: {len(planted)} planted defects")
