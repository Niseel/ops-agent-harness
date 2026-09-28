"""Logging to stdout: JSON lines (default) or plain text.

Pass run_id / node / tool / attempt / seq / kind / status / attention / data
with `extra=` and they become JSON fields, so one run can be filtered out of
the stream (`jq 'select(.run_id=="…")'`). Known secret values are masked in
the whole line, exception text included.
"""

import json
import logging
import sys

from app.clock import now_iso

FIELDS = ("run_id", "node", "tool", "attempt", "seq", "kind", "status", "attention", "data")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out = {
            "ts": now_iso(record.created),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        out |= {k: v for k in FIELDS if (v := getattr(record, k, None)) is not None}
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, default=str, ensure_ascii=False)


class TextFormatter(logging.Formatter):
    def __init__(self) -> None:
        super().__init__("%(asctime)s %(levelname)s %(name)s: %(message)s")

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        return now_iso(record.created)  # the project format, not local time


def secret_forms(secrets: list[str]) -> list[str]:
    """Each secret as typed and JSON-escaped (JSON escapes quotes, backslashes and control characters),
    longest first, so a secret that contains another is masked whole. Shared by logs and API responses."""
    forms = {form for s in secrets if s for form in (s, json.dumps(s, ensure_ascii=False)[1:-1])}
    return sorted(forms, key=len, reverse=True)


class _Masked(logging.Formatter):
    """Wraps another formatter and masks secrets in its finished output."""

    def __init__(self, inner: logging.Formatter, secrets: list[str]) -> None:
        super().__init__()
        self.inner = inner
        self.secrets = secret_forms(secrets)

    def format(self, record: logging.LogRecord) -> str:
        line = self.inner.format(record)
        for secret in self.secrets:
            line = line.replace(secret, "***")
        return line


def setup(level: str, fmt: str, secrets: list[str]) -> None:
    inner = JsonFormatter() if fmt == "json" else TextFormatter()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_Masked(inner, secrets))
    logging.basicConfig(level=level, handlers=[handler], force=True)
    # uvicorn installs its own unmasked handlers before it imports the app; send its lines (tracebacks and
    # access paths included) through the masked root instead.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        server_log = logging.getLogger(name)
        server_log.handlers.clear()
        server_log.propagate = True
