"""Shared test helpers for the full-review effort (Phase 1.4).

- QueryCounter: counts SQL statements executed on an engine while active.
- parallel_requests / parallel_calls: fire N concurrent operations with
  asyncio.gather for race-condition tests (real Postgres, real app).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from typing import Any

import httpx
from sqlalchemy import event

__all__ = ["QueryCounter", "parallel_calls", "parallel_requests"]


class QueryCounter:
    """Count every SQL statement executed on an engine between start() and stop().

    Usage:
        counter = QueryCounter(engine)
        with counter:
            await client.get("/api/v1/...")
        assert counter.count == EXPECTED  # constant, not growing with rows
    """

    def __init__(self, engine: Any) -> None:
        # async engine -> underlying sync engine carries the events
        self._sync_engine = engine.sync_engine if hasattr(engine, "sync_engine") else engine
        self.count = 0
        self.statements: list[str] = []
        self._listening = False
        # event.listen() keeps only a WEAK reference to the handler — hold a
        # strong reference to the bound method or the listener is GC'd silently.
        self._ref = self._on_execute

    def _on_execute(self, conn, cursor, statement, parameters, context, executemany) -> None:
        self.count += 1
        self.statements.append(statement)

    def __enter__(self) -> QueryCounter:
        if not self._listening:
            event.listen(self._sync_engine, "before_cursor_execute", self._ref)
            self._listening = True
        self.count = 0
        self.statements.clear()
        return self

    def __exit__(self, *exc_info: object) -> None:
        if self._listening:
            event.remove(self._sync_engine, "before_cursor_execute", self._ref)
            self._listening = False

    async def __aenter__(self) -> QueryCounter:
        return self.__enter__()

    async def __aexit__(self, *exc_info: object) -> None:
        self.__exit__()


async def parallel_calls(
    n: int,
    factory: Callable[[int], Coroutine[Any, Any, Any]],
    *,
    return_exceptions: bool = True,
) -> list[Any]:
    """Run n coroutines created by factory(i) concurrently on the event loop.

    Race tests use this against a real Postgres — the DB serializes writes,
    so results expose exactly which requests won/lost and with which status.
    """
    return list(
        await asyncio.gather(*(factory(i) for i in range(n)), return_exceptions=return_exceptions)
    )


async def parallel_requests(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    n: int,
    *,
    headers: dict[str, str] | None = None,
    json_payload: Any = None,
    **kwargs: Any,
) -> list[Any]:
    """Fire n identical concurrent HTTP requests and return all responses.

    Exceptions (not HTTP error statuses) are returned in the list so callers
    can assert on both success and failure counts.
    """

    async def one(_: int) -> Any:
        return await client.request(method, url, headers=headers, json=json_payload, **kwargs)

    return await parallel_calls(n, one)
