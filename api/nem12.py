"""Minimal NEM12 reader (AEMO MDFF): 100 header, 200 NMI blocks, 300 interval days, 400 quality, 900 end."""
import csv
import io
from datetime import datetime


def parse(text):
    out = {"has_100": False, "has_900": False, "nmi": None, "rows": []}
    block = None
    for line_no, rec in enumerate(csv.reader(io.StringIO(text)), 1):
        if not rec:
            continue
        kind = rec[0].strip()
        if kind == "100":
            out["has_100"] = True
        elif kind == "900":
            out["has_900"] = True
        elif kind == "200":
            block = {"nmi": rec[1], "suffix": rec[4], "interval": int(rec[8] or 30)}
            out["nmi"] = out["nmi"] or rec[1]
        elif kind == "300" and block:
            # the tail after the values is: quality, reason code, reason text, update time, MSATS load time
            values, tail = rec[2:-5], rec[-5:]
            out["rows"].append({
                **block,
                "date": datetime.strptime(rec[1], "%Y%m%d").date(),
                "values": [float(v) if v.strip() else None for v in values],
                "quality": tail[0][:1] or "A",
                "line": line_no,
                "interval_quality": {},
            })
        elif kind == "400" and out["rows"]:
            start, end, quality = int(rec[1]), int(rec[2]), rec[3][:1]
            for i in range(start, end + 1):
                out["rows"][-1]["interval_quality"][i - 1] = quality
    return out
