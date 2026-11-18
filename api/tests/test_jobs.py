
import proof
from worker import can_move

FLOW = ["queued", "parsing", "validated", "priced", "done"]


def test_happy_path_and_no_skipping():
    assert all(can_move(a, b) for a, b in zip(FLOW, FLOW[1:], strict=False))
    assert not can_move("queued", "validated")
    assert not can_move("parsing", "done")
    assert can_move("parsing", "quarantined")


def test_terminal_states_stay_put():
    for end in ("done", "failed", "quarantined"):
        assert not any(can_move(end, s) for s in FLOW + ["failed"])


def test_upload_is_idempotent(fresh):
    r = proof.idempotency(fresh)
    assert r == {"uploads": 5, "files": 1, "jobs": 1, "rows_before": 35040, "rows_after": 35040, "same_id": True}


def test_worker_killed_mid_parse_retries_cleanly(fresh):
    r = proof.kill_retry(fresh)
    assert r["state_after_kill"] == "parsing" and r["rows_after_kill"] == 0
    assert r["state"] == "done" and r["attempts"] == 2
    assert r["rows"] == r["distinct"] == 35040
