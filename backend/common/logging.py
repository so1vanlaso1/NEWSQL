"""Structured logging for the SQLNEW backend (Phase 9).

One place to configure logging so every module can ``get_logger(__name__)`` and get:

- a **console** handler (human-readable) and, when ``LOG_FILE`` is set, a **JSON-lines**
  file handler (one JSON object per record) for grep/ingestion;
- **contextual fields** — ``request_id``, ``conversation_id``, ``turn_id``, ``review_id`` —
  carried on :mod:`contextvars` so they attach to every log record emitted while handling
  a request, without threading them through call signatures.

Usage::

    from backend.common.logging import setup_logging, get_logger, bind_context
    setup_logging()                       # once, at app startup
    log = get_logger(__name__)
    with bind_context(conversation_id=cid, turn_id=tid):
        log.info("turn started")

The FastAPI request-id middleware lives in :func:`RequestIdMiddleware`.
"""
from __future__ import annotations

import contextlib
import contextvars
import json
import logging
import logging.handlers
import time
import uuid
from typing import Iterator, Optional

from backend import config

# ---- context variables ------------------------------------------------------
# Default "" so a record emitted outside any request still formats cleanly.
_request_id: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="")
_conversation_id: contextvars.ContextVar[str] = contextvars.ContextVar("conversation_id", default="")
_turn_id: contextvars.ContextVar[str] = contextvars.ContextVar("turn_id", default="")
_review_id: contextvars.ContextVar[str] = contextvars.ContextVar("review_id", default="")

_CONTEXT_VARS = {
    "request_id": _request_id,
    "conversation_id": _conversation_id,
    "turn_id": _turn_id,
    "review_id": _review_id,
}


def new_request_id() -> str:
    return uuid.uuid4().hex[:12]


def set_request_id(value: str) -> None:
    _request_id.set(value or "")


def get_request_id() -> str:
    return _request_id.get()


def push_request_id(value: str):
    """Bind a request id and return a token to restore the previous value with pop_request_id."""
    return _request_id.set(value or "")


def pop_request_id(token) -> None:
    _request_id.reset(token)


@contextlib.contextmanager
def bind_context(**fields: Optional[str]) -> Iterator[None]:
    """Temporarily bind contextual log fields (request_id/conversation_id/turn_id/review_id).

    Only known keys are applied; unknown keys are ignored. Restores the previous values
    on exit even if the body raises.
    """
    tokens = []
    for key, value in fields.items():
        var = _CONTEXT_VARS.get(key)
        if var is not None and value is not None:
            tokens.append((var, var.set(str(value))))
    try:
        yield
    finally:
        for var, token in reversed(tokens):
            var.reset(token)


# ---- logging plumbing -------------------------------------------------------
class _ContextFilter(logging.Filter):
    """Attach the current context-var values to every record as attributes."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003 - stdlib name
        for key, var in _CONTEXT_VARS.items():
            setattr(record, key, var.get())
        return True


_RESERVED = set(vars(logging.makeLogRecord({})).keys()) | {
    "message", "asctime", "taskName",
}


class JsonFormatter(logging.Formatter):
    """One JSON object per record; contextual + any extra fields are included."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(record.created))
            + f".{int(record.msecs):03d}",
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key in _CONTEXT_VARS:
            val = getattr(record, key, "")
            if val:
                payload[key] = val
        # Any ad-hoc extras passed via logger.info(..., extra={...}).
        for key, val in record.__dict__.items():
            if key not in _RESERVED and key not in _CONTEXT_VARS and not key.startswith("_"):
                if key in payload:
                    continue
                try:
                    json.dumps(val)
                    payload[key] = val
                except (TypeError, ValueError):
                    payload[key] = str(val)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


class ConsoleFormatter(logging.Formatter):
    """Human-readable, with a compact ``[req … conv … turn …]`` context tag."""

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        bits = []
        for short, key in (("req", "request_id"), ("conv", "conversation_id"),
                           ("turn", "turn_id"), ("rev", "review_id")):
            val = getattr(record, key, "")
            if val:
                bits.append(f"{short}={val}")
        return f"{base}  [{' '.join(bits)}]" if bits else base


_CONFIGURED = False


def setup_logging(force: bool = False) -> None:
    """Configure the root logger once (idempotent). Safe to call at import or startup."""
    global _CONFIGURED
    if _CONFIGURED and not force:
        return

    root = logging.getLogger()
    root.setLevel(getattr(logging, config.LOG_LEVEL, logging.INFO))
    # Clear handlers we may have added on a prior call (e.g. reload); leave others.
    for h in list(root.handlers):
        if getattr(h, "_sqlnew", False):
            root.removeHandler(h)

    ctx_filter = _ContextFilter()

    console = logging.StreamHandler()
    console._sqlnew = True  # type: ignore[attr-defined]
    console.addFilter(ctx_filter)
    if config.LOG_FORMAT == "json":
        console.setFormatter(JsonFormatter())
    else:
        console.setFormatter(ConsoleFormatter(
            "%(asctime)s %(levelname)-7s %(name)s: %(message)s", datefmt="%H:%M:%S"))
    root.addHandler(console)

    if config.LOG_FILE:
        try:
            config.LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
            fileh = logging.handlers.RotatingFileHandler(
                config.LOG_FILE, maxBytes=5_000_000, backupCount=3, encoding="utf-8")
            fileh._sqlnew = True  # type: ignore[attr-defined]
            fileh.addFilter(ctx_filter)
            fileh.setFormatter(JsonFormatter())
            root.addHandler(fileh)
        except OSError as exc:  # never let a bad log path crash startup
            root.warning("could not open LOG_FILE %s: %s", config.LOG_FILE, exc)

    # Tame noisy libraries.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    setup_logging()
    return logging.getLogger(name)


# The FastAPI request-id middleware lives in backend/app.py (a BaseHTTPMiddleware
# subclass) and uses push_request_id/pop_request_id above, so this module stays free
# of a hard Starlette import.
