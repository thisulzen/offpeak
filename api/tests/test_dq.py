import pytest

import dq
import gen_nem12
import nem12

HEAD = "100,NEM12,202601010000,A,B\n200,4103000001,E1,E1,E1,N1,M1,KWH,30,\n"
DAY = ",".join(["0.500"] * 48)


def run(text):
    _, issues, quarantine = dq.check(nem12.parse(text))
    return {(i["rule"], i["row_ref"]) for i in issues}, quarantine


def file(*rows, footer=True):
    return HEAD + "".join(f"300,{r},A,,,,\n" if r.count(",") > 1 else f"300,{r},{DAY},A,,,,\n" for r in rows) + ("900\n" if footer else "")


def test_parse_reads_values_and_direction():
    intervals, issues, _ = dq.check(nem12.parse(file("20260105")))
    assert len(intervals) == 48 and not issues
    nmi, ts, kwh, direction, quality = intervals[1]
    assert (nmi, ts.strftime("%H:%M"), kwh, direction, quality) == ("4103000001", "00:30", 0.5, "import", "A")


@pytest.mark.parametrize("text, expected", [
    (file("20260105", "20260107"), ("missing", "E1 20260106")),
    (file("20260105", "20260105"), ("duplicate", "E1 20260105")),
    (file("20260105," + ",".join(["-0.1"] + ["0.5"] * 47)), ("negative", "E1 20260105")),
    (file("20260105").replace(",A,", ",E,"), ("estimated", "E1 20260105")),
    (file("20260105", footer=False), ("no_900", "file")),
    (file("20260105," + ",".join(["0.5"] * 40)), ("interval_mismatch", "E1 20260105")),
    (file("20251005," + ",".join(["0.5"] * 46)), ("dst_gap", "E1 20251005")),
    (file("20260405," + ",".join(["0.5"] * 50)), ("dst_gap", "E1 20260405")),
])
def test_each_rule(text, expected):
    found, _ = run(text)
    assert found == {expected}


def test_400_record_marks_substituted_intervals():
    text = file("20260105").replace("900\n", "400,1,4,S,51,,\n900\n")
    assert run(text)[0] == {("estimated", "E1 20260105")}


def test_generated_defects_all_caught_and_clean_file_is_clean():
    clean, _ = gen_nem12.generate(days=60, start=gen_nem12.date(2025, 9, 1))
    assert run(clean) == (set(), False)
    broken, planted = gen_nem12.generate(defects=True)
    found, quarantine = run(broken)
    assert found == set(planted) and len(planted) == 17 and not quarantine


def test_too_many_lost_days_quarantines():
    rows = [f"202601{d:02}" for d in range(1, 11)]
    del rows[2:5]  # 3 of 10 days missing
    assert run(file(*rows))[1] is True
