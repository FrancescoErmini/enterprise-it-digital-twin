"""Execution limits: timeout, row cap, result-size cap, concurrency."""

import dataclasses
import threading

import pytest

from app.technology_graph.client import GraphBusyError, GraphClient, GraphQueryError


def test_row_cap_truncates(client):
    result = client.read("UNWIND range(1, 5000) AS x RETURN x", max_rows=100)
    assert result.row_count == 100 and result.truncated


def test_default_row_cap_from_settings(platform):
    out = platform.queries.run("UNWIND range(1, 100000) AS x RETURN x AS value", question="row cap test")
    assert out["ok"] and out["row_count"] == platform.settings.query_max_rows and out["truncated"]


def test_timeout_is_enforced(settings):
    fast = GraphClient(dataclasses.replace(settings, query_timeout_s=1.0))
    try:
        with pytest.raises(GraphQueryError) as exc:
            fast.read("UNWIND range(1, 200000000) AS x WITH x WHERE x % 7 = 123456789 RETURN count(x) AS c")
        assert "Timed" in (exc.value.code or "") or "timeout" in str(exc.value).lower()
    finally:
        fast.close()


def test_result_size_cap(settings):
    small = GraphClient(dataclasses.replace(settings, query_max_result_bytes=2000))
    try:
        result = small.read("UNWIND range(1, 400) AS x RETURN x, 'padding-padding-padding' AS p")
        assert result.truncated and result.row_count < 400
    finally:
        small.close()


def test_concurrency_cap(settings):
    """With one slot busy, a second query waits at most its timeout and is then refused."""
    one = GraphClient(dataclasses.replace(settings, query_max_concurrency=1, query_timeout_s=0.3))
    started = threading.Event()

    def slow():
        started.set()
        try:
            one.read("UNWIND range(1, 300000000) AS x WITH x WHERE x = -1 RETURN count(x) AS c", timeout_s=3.0)
        except GraphQueryError:
            pass  # server-side timeout is expected

    try:
        t = threading.Thread(target=slow)
        t.start()
        started.wait()
        threading.Event().wait(0.3)
        with pytest.raises(GraphBusyError):
            one.read("RETURN 1 AS x")
        t.join()
    finally:
        one.close()
