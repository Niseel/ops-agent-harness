"""Trace events (ADR 0014): one call stores the event, hands it to live
subscribers (the SSE stream in M3) and writes one JSON log line.

Secret values are masked before the event goes anywhere.
"""

import asyncio
import logging
import time
from collections import defaultdict
from typing import Any

from app.harness.store import Store, now_iso

log = logging.getLogger("app.trace")
_LEVELS = {"error": logging.ERROR, "warn": logging.WARNING}


class Tracer:
    def __init__(self, store: Store, secrets: list[str]) -> None:
        self.store = store
        self.secrets = [s for s in secrets if s]
        self._subscribers: dict[str, set[asyncio.Queue]] = defaultdict(set)

    def mask(self, value: Any) -> Any:
        """Replace every secret in strings, walking dicts and lists (not their JSON text)."""
        if isinstance(value, str):
            for secret in self.secrets:
                value = value.replace(secret, "***")
            return value
        if isinstance(value, dict):
            return {key: self.mask(item) for key, item in value.items()}
        if isinstance(value, list | tuple):
            return [self.mask(item) for item in value]
        return value

    async def emit(
        self,
        run_id: str | None,
        kind: str,
        *,
        node: str | None = None,
        tool: str | None = None,
        status: str | None = None,
        attention: str | None = None,
        msg: str | None = None,
        data: Any = None,
    ) -> dict:
        now = time.time()
        event = {
            "run_id": run_id,
            "t_ms": int(now * 1000),  # Unix epoch milliseconds
            "kind": kind,
            "node": node,
            "tool": tool,
            "status": status,
            "attention": attention,
            "msg": self.mask(msg),
            "data": self.mask(data),
            "created_at": now_iso(now),  # the same moment, ISO-8601 UTC
        }
        event["seq"] = await self.store.insert_event(event)
        for queue in self._subscribers.get(run_id, ()):
            queue.put_nowait(event)
        log.log(
            _LEVELS.get(attention, logging.INFO),
            event["msg"] or kind,
            extra={k: event[k] for k in ("run_id", "node", "tool", "seq", "kind", "status", "attention", "data")},
        )
        return event

    def subscribe(self, run_id: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        self._subscribers[run_id].add(queue)
        return queue

    def unsubscribe(self, run_id: str, queue: asyncio.Queue) -> None:
        subscribers = self._subscribers.get(run_id)
        if subscribers is not None:
            subscribers.discard(queue)
            if not subscribers:
                del self._subscribers[run_id]
