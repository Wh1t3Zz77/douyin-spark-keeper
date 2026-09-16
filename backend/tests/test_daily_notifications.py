from datetime import datetime, timedelta
from types import SimpleNamespace

from app.worker import task_due_for_daily_report


def test_plan_created_after_send_time_starts_tomorrow():
    utc_start = datetime(2026, 9, 9, 16, 0)
    task = SimpleNamespace(send_time="14:25", created_at=utc_start + timedelta(hours=17, minutes=27))
    assert task_due_for_daily_report(task, utc_start) is False


def test_plan_created_before_send_time_is_due_today():
    utc_start = datetime(2026, 9, 9, 16, 0)
    task = SimpleNamespace(send_time="14:25", created_at=utc_start + timedelta(hours=10))
    assert task_due_for_daily_report(task, utc_start) is True
