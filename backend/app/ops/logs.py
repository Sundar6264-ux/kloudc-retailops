"""Structured logging: every log record carries the request id and the
store code, as readable text (LOG_FORMAT=text, the default) or one JSON
object per line (LOG_FORMAT=json). With LOG_DIR set, app.log (INFO and up)
and error.log (ERROR and up, with tracebacks) are written there too,
rotated by size.

``configure_logging`` is called once from app/main.py. It only adds to the
root logger (pytest's caplog and uvicorn's own loggers keep working).
"""

import contextvars
import datetime as dt
import json
import logging
import logging.handlers
import pathlib
import re
import uuid
from typing import Optional

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("retailops_request_id", default="-")

REQUEST_ID_HEADER = "X-Request-ID"
_INCOMING_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{8,64}$")
TEXT_FORMAT = "%(asctime)s %(levelname)s %(name)s [%(store)s %(request_id)s] %(message)s"
APP_LOG = "app.log"
ERROR_LOG = "error.log"
_MAX_BYTES = 10 * 1024 * 1024

_state = {"factory_installed": False, "configured": None}


def new_request_id() -> str:
    return uuid.uuid4().hex


def accept_request_id(incoming: Optional[str]) -> str:
    """The caller's X-Request-ID when it is a plain token (8-64 of
    letters, digits, . _ : -), else a new one - never log what a client
    could use to forge or break a log line."""
    value = (incoming or "").strip()
    return value if _INCOMING_ID_RE.match(value) else new_request_id()


def current_request_id() -> str:
    return request_id_var.get()


def _current_store() -> str:
    try:
        from app.tenancy.context import current_store_code

        return current_store_code() or "-"
    except Exception:  # noqa: BLE001 - logging must never fail
        return "-"


def _install_record_factory() -> None:
    """Every LogRecord gets ``request_id`` and ``store`` attributes (set
    here, not in a handler filter, so any handler's format can use them)."""
    if _state["factory_installed"]:
        return
    previous = logging.getLogRecordFactory()

    def factory(*args, **kwargs):
        record = previous(*args, **kwargs)
        if not hasattr(record, "request_id"):
            record.request_id = request_id_var.get()
        if not hasattr(record, "store"):
            record.store = _current_store()
        return record

    logging.setLogRecordFactory(factory)
    _state["factory_installed"] = True


class JsonFormatter(logging.Formatter):
    """One JSON object per line: ts (UTC ISO), level, logger, msg,
    request_id, store, plus exc (traceback text) and any ``extra`` fields
    given as ``extra={"fields": {...}}``."""

    def format(self, record: logging.LogRecord) -> str:
        out = {
            "ts": dt.datetime.fromtimestamp(record.created, dt.timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
            "store": getattr(record, "store", "-"),
        }
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            for k, v in fields.items():
                out.setdefault(str(k), v)
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, ensure_ascii=False, default=str)


def make_formatter(fmt: str) -> logging.Formatter:
    return JsonFormatter() if fmt == "json" else logging.Formatter(TEXT_FORMAT)


def configure_logging(settings=None, *, force: bool = False) -> dict:
    """Idempotent. Returns what was set up ({"format", "level", "log_dir"})."""
    if settings is None:
        from app.config import process_settings

        settings = process_settings()
    fmt = (getattr(settings, "log_format", "text") or "text").lower()
    level_name = (getattr(settings, "log_level", "INFO") or "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    log_dir = (getattr(settings, "log_dir", "") or "").strip()
    wanted = {"format": fmt, "level": level_name, "log_dir": log_dir}
    if _state["configured"] == wanted and not force:
        return wanted
    _install_record_factory()
    root = logging.getLogger()
    root.setLevel(level)
    for h in list(root.handlers):
        if getattr(h, "_retailops", False):
            root.removeHandler(h)
            h.close()
    formatter = make_formatter(fmt)
    # stdout: only when nothing else (pytest, an embedding app) set up the root.
    if not [h for h in root.handlers if not getattr(h, "_retailops", False)]:
        stream = logging.StreamHandler()
        stream.setFormatter(formatter)
        stream._retailops = True  # type: ignore[attr-defined]
        root.addHandler(stream)
    if log_dir:
        folder = pathlib.Path(log_dir).expanduser()
        folder.mkdir(parents=True, exist_ok=True)
        for name, lvl, count in ((APP_LOG, logging.INFO, 5), (ERROR_LOG, logging.ERROR, 10)):
            fh = logging.handlers.RotatingFileHandler(folder / name, maxBytes=_MAX_BYTES, backupCount=count,
                                                      encoding="utf-8")
            fh.setLevel(lvl)
            fh.setFormatter(formatter)
            fh._retailops = True  # type: ignore[attr-defined]
            root.addHandler(fh)
    _state["configured"] = wanted
    return wanted
