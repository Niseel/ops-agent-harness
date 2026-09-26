"""Logging to stdout: JSON lines (default) or plain text.

Pass run_id / node / tool / attempt with `extra=` and they become JSON fields,
so one run can be filtered out of the stream (`jq 'select(.run_id=="…")'`).
Known secret values are masked in every line.
"""

import json
import logging
import sys
from datetime import UTC, datetime

FIELDS = ("run_id", "node", "tool", "attempt")


class _Redact(logging.Filter):
    def __init__(self, secrets: list[str]) -> None:
        super().__init__()
        self.secrets = secrets

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        for s in self.secrets:
            msg = msg.replace(s, "***")
        record.msg, record.args = msg, None
        return True


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


def setup(level: str, fmt: str, secrets: list[str]) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(_Redact(secrets))
    handler.setFormatter(
        JsonFormatter() if fmt == "json" else logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    logging.basicConfig(level=level, handlers=[handler], force=True)
