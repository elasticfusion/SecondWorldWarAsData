"""Regression — §1.0c WARNING: batch_poller stall-alert cadence + 24h-stuck force-complete.

(1) _check_stalled's old `int(age_hours)==int(age_hours-5/60)` rate-limit assumed a 5-min poll; the
    real schedule is 15-30 min, so the hourly ">4h stalled" alert was often skipped. Now it tracks a
    persisted last_stall_notify_hour and notifies once per elapsed-hour regardless of cadence.
(2) The 24h-stuck force-complete returned 'complete' with success+error<total and NO alert, so the
    ecs mapper count-mismatched + silently discarded the completed fraction. Now it alerts loudly.

batch_poller is imported LAZILY inside each test (matching tests/unit/test_batch_poller.py) so the
module import never happens before a sibling test's moto mock_aws context.
"""

import time
from unittest import mock


def test_force_complete_if_stuck_alerts_on_missing():
    import lambda_handlers.batch_poller as BP

    notes = []
    with mock.patch.object(BP, "_notify", lambda m: notes.append(m)):
        submitted = int(time.time()) - 30 * 3600  # 30h old, 7/10 done
        out = BP._force_complete_if_stuck(
            "batch_abc", submitted, success=7, error=0, total=10
        )
    assert out == "complete"
    assert notes and "MISSING" in notes[0] and "force-completed" in notes[0]


def test_force_complete_if_stuck_not_triggered_when_fresh():
    import lambda_handlers.batch_poller as BP

    out = BP._force_complete_if_stuck("b", int(time.time()) - 3600, 5, 0, 10)  # 1h old
    assert out is None


def test_check_stalled_notifies_once_per_hour():
    import lambda_handlers.batch_poller as BP

    job = {
        "batch_id": "b1",
        "submitted_at": int(time.time()) - int(5.2 * 3600),
        "book": "X",
        "request_count": 10,
        "last_stall_notify_hour": 4,
    }
    notes = []
    persisted = {}
    resp = mock.Mock(ok=True)
    resp.json.return_value = {"state": {"num_success": 6, "num_error": 0}}
    fake_table = mock.Mock()
    fake_table.update_item = lambda **kw: persisted.update(
        kw["ExpressionAttributeValues"]
    )
    with (
        mock.patch.object(BP, "_notify", lambda m: notes.append(m)),
        mock.patch("requests.get", lambda *a, **k: resp),
        mock.patch(
            "lambda_handlers.batch_poller.boto3.resource",
            lambda *a, **k: mock.Mock(Table=lambda t: fake_table),
        ),
    ):
        BP._check_stalled(job, "key")
        assert (
            len(notes) == 1 and persisted.get(":h") == 5
        )  # notified hour 5 + persisted
        job["last_stall_notify_hour"] = 5
        notes.clear()
        BP._check_stalled(job, "key")
        assert notes == []  # suppressed (already alerted this elapsed-hour)
