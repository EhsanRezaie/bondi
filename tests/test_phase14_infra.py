"""Phase 1.4 smoke tests — prove the new test infrastructure works.

Not feature tests: they validate QueryCounter, parallel helpers, and the
blockbuster guard so later phases can rely on them.
"""

import asyncio
import time

import pytest
from sqlalchemy import text

from tests.helpers import QueryCounter, parallel_calls

pytestmark = pytest.mark.asyncio


async def test_query_counter_counts_statements(db_engine):
    counter = QueryCounter(db_engine)
    async with counter, db_engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
        await conn.execute(text("SELECT 2"))
    assert counter.count == 2
    assert len(counter.statements) == 2


async def test_query_counter_isolates_scope(db_engine):
    async with db_engine.connect() as conn:
        await conn.execute(text("SELECT 1"))  # outside scope — not counted
    counter = QueryCounter(db_engine)
    async with counter:
        pass
    assert counter.count == 0


async def test_parallel_calls_runs_concurrently():
    async def one(i: int) -> int:
        await asyncio.sleep(0.05)
        return i

    start = time.monotonic()
    results = await parallel_calls(10, one)
    elapsed = time.monotonic() - start

    assert results == list(range(10))
    # 10 x 50ms sequential would be ~0.5s; concurrent finishes far sooner
    assert elapsed < 0.3, f"not concurrent (took {elapsed:.2f}s)"


@pytest.mark.blockbuster
async def test_blockbuster_marker_detects_blocking_io(tmp_path):
    """Blocking I/O in app code must raise BlockingError.

    Guard scans app/ frames only (3rd-party internals excluded).
    Probe is ``_delete_file`` — the one system.py file helper without an
    ``except Exception`` block (the read/write helpers log on failure,
    which swallows the guard's error under pytest's excluded stdout).
    """
    from blockbuster import BlockingError

    from app.api.v1.endpoints.system import _delete_file

    probe = tmp_path / "blockbuster_probe.json"
    probe.write_text('{"a": 1}')

    with pytest.raises(BlockingError):
        _delete_file(probe)  # sync unlink on event loop → guard must catch
