"""Per-topic cooperative run deadline and bounded HTTP I/O.

Timeouts are on socket operations, not a kill switch for CPU rendering.
The production worker checks cancellation/deadline between steps and keeps
already-rendered MP4s. This is intentionally isolated from other requests.
"""
from __future__ import annotations
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import time

class BudgetExpired(RuntimeError):
    pass

class RunCancelled(RuntimeError):
    pass

@dataclass(frozen=True)
class _RunLimit:
    deadline:float
    cancel:object=None

_CURRENT=ContextVar("thse_run_limit",default=None)

@contextmanager
def topic_budget(seconds=210,cancel=None):
    if seconds<=0:raise ValueError("Invalid QA time budget")
    token=_CURRENT.set(_RunLimit(time.monotonic()+float(seconds),cancel))
    try:
        yield
    finally:
        _CURRENT.reset(token)

def budget_active():
    return _CURRENT.get() is not None

def remaining_seconds():
    limit=_CURRENT.get()
    return None if limit is None else max(0.0,limit.deadline-time.monotonic())

def check_budget():
    limit=_CURRENT.get()
    if limit is None:return
    if limit.cancel is not None and limit.cancel.is_set():
        raise RunCancelled("Đã nhận yêu cầu dừng. Nội dung và MP4 đã tạo vẫn được giữ.")
    if time.monotonic()>=limit.deadline:
        raise BudgetExpired("Hết giới hạn thời gian kiểm chứng cho chủ đề này. Các câu đã tạo vẫn được lưu để duyệt.")

def network_timeout(inside=20,outside=60):
    check_budget()
    remaining=remaining_seconds()
    if remaining is None:return float(outside)
    return max(.25,min(float(inside),remaining))

def check_cancel(cancel):
    if cancel is not None and cancel.is_set():
        raise RunCancelled("Đã nhận yêu cầu dừng. Video đã hoàn thành vẫn được giữ.")
