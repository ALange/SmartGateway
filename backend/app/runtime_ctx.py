"""Tiny context flags shared between modules (kept separate to avoid import cycles)."""
from contextvars import ContextVar

# True inside a stress-test request: the request is routed normally but is NOT written to the request log
# (synthetic load must not distort dashboards, cache stats or the log).
no_request_log: ContextVar[bool] = ContextVar("no_request_log", default=False)

# True for requests made by the signed-in admin (Test dialog, Playground, stress test): they get the detailed upstream error text and the
# routing headers. Everyone else on the public gateway URLs gets generic errors, so internal hostnames, upstream names and upstream
# response bodies never leak. SG_VERBOSE_ERRORS=1 turns the details on for everybody (debugging on a private network only).
import os as _os
verbose: ContextVar[bool] = ContextVar("verbose", default=_os.environ.get("SG_VERBOSE_ERRORS", "").lower() in ("1", "true", "yes"))
