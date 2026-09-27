"""Logging to stdout: JSON lines (default) or plain text.

Pass run_id / node / tool / attempt / seq / kind / status / attention / data
with `extra=` and they become JSON fields, so one run can be filtered out of
the stream (`jq 'select(.run_id=="…")'`). Known secret values are masked in
the whole line, exception text included.
"""

import json
import logging
import sys
from datetime import UTC, datetime

FIELDS = ("run_id", "node", "tool", "attempt", "seq", "kind", "status", "attention", "data")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        out |= {k: v for k in FIELDS if (v := getattr(record, k, None)) is not None}
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, default=str, ensure_ascii=False)


class _Masked(logging.Formatter):
    """Wraps another formatter and masks secrets in its finished output."""

    def __init__(self, inner: logging.Formatter, secrets: list[str]) -> None:
        super().__init__()
        self.inner = inner
        # JSON escapes quotes, backslashes and control characters, so look for the escaped form too.
        forms = {form for s in secrets if s for form in (s, json.dumps(s, ensure_ascii=False)[1:-1])}
        self.secrets = sorted(forms, key=len, reverse=True)

    def format(self, record: logging.LogRecord) -> str:
        line = self.inner.format(record)
        for secret in self.secrets:
            line = line.replace(secret, "***")
        return line


def setup(level: str, fmt: str, secrets: list[str]) -> None:
    inner = JsonFormatter() if fmt == "json" else logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_Masked(inner, secrets))
    logging.basicConfig(level=level, handlers=[handler], force=True)
