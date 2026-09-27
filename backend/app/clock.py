"""The project's one timestamp format: ISO-8601 UTC with milliseconds and a `Z` suffix.

Example: 2026-09-27T09:00:00.123Z. Every timestamp the app stores, logs or returns
comes from `now_iso`, so all of them look the same and sort correctly as text.
Event `t_ms` (Unix epoch milliseconds) is the only other time value.
"""

from datetime import UTC, datetime

# What `now_iso` returns. Output models use it to check timestamps that come from tools.
ISO_PATTERN = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$"


def now_iso(ts: float | None = None) -> str:
    """Now, or the Unix time `ts`, in the project's format."""
    moment = datetime.fromtimestamp(ts, UTC) if ts is not None else datetime.now(UTC)
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")
