"""Tiny context flags shared between modules (kept separate to avoid import cycles)."""
from contextvars import ContextVar

# True inside a stress-test request: the request is routed normally but is NOT written to the request log
# (synthetic load must not distort dashboards, cache stats or the log).
no_request_log: ContextVar[bool] = ContextVar("no_request_log", default=False)
