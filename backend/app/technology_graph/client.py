"""Read-only access to the technology graph.

Every transaction is opened in READ access mode as the dedicated agent user.
Neo4j itself rejects any write, schema or administration statement in a READ
transaction (``Neo.ClientError.Statement.AccessMode``); that server-side refusal
is the primary control. Validation (see validator.py) runs before this layer.
"""

from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import neo4j
from neo4j.graph import Node, Path, Relationship

from app.core.config import Settings

_ID_COLUMN = re.compile(r"(^|_)ids?$", re.IGNORECASE)


class GraphQueryError(RuntimeError):
    def __init__(self, message: str, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


class GraphBusyError(GraphQueryError):
    pass


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[dict[str, Any]]
    row_count: int
    truncated: bool
    duration_ms: float
    evidence_ids: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"columns": self.columns, "rows": self.rows, "row_count": self.row_count,
                "truncated": self.truncated, "duration_ms": round(self.duration_ms, 1),
                "evidence_ids": self.evidence_ids}


@dataclass
class ExplainResult:
    ok: bool
    query_type: str | None = None
    unknown_labels: list[str] = field(default_factory=list)
    unknown_relationship_types: list[str] = field(default_factory=list)
    unknown_properties: list[str] = field(default_factory=list)
    error_code: str | None = None
    error_message: str | None = None


_UNKNOWN_NAME = re.compile(r"`([^`]+)`")


def _serialize(value: Any, evidence: set[str]) -> Any:
    if isinstance(value, Node):
        props = dict(value.items())
        if isinstance(props.get("id"), str):
            evidence.add(props["id"])
        return {"_labels": sorted(value.labels), **{k: _serialize(v, evidence) for k, v in props.items()}}
    if isinstance(value, Relationship):
        return {"_type": value.type,
                "_start": value.start_node.get("id") if value.start_node is not None else None,
                "_end": value.end_node.get("id") if value.end_node is not None else None,
                **{k: _serialize(v, evidence) for k, v in value.items()}}
    if isinstance(value, Path):
        return {"nodes": [_serialize(n, evidence) for n in value.nodes],
                "relationships": [_serialize(r, evidence) for r in value.relationships]}
    if isinstance(value, list):
        return [_serialize(v, evidence) for v in value]
    if isinstance(value, dict):
        return {k: _serialize(v, evidence) for k, v in value.items()}
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)  # temporal and spatial types


def _collect_id_columns(row: dict[str, Any], evidence: set[str]) -> None:
    for key, value in row.items():
        if not _ID_COLUMN.search(key):
            continue
        values = value if isinstance(value, list) else [value]
        for v in values:
            if isinstance(v, str) and v:
                evidence.add(v)


class GraphClient:
    def __init__(self, settings: Settings, driver: neo4j.Driver | None = None) -> None:
        self.settings = settings
        self.database = settings.neo4j_database
        self.driver = driver or neo4j.GraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_reader_user, settings.neo4j_reader_password),
            max_connection_pool_size=max(2, settings.query_max_concurrency * 2),
            connection_acquisition_timeout=settings.query_timeout_s,
            user_agent="digital-twin-api",
        )
        self._slots = threading.BoundedSemaphore(settings.query_max_concurrency)

    def close(self) -> None:
        self.driver.close()

    def ping(self) -> bool:
        try:
            self.read("RETURN 1 AS ok", max_rows=1)
            return True
        except Exception:  # noqa: BLE001
            return False

    def read(self, cypher: str, params: dict[str, Any] | None = None, *,
             timeout_s: float | None = None, max_rows: int | None = None) -> QueryResult:
        """Run one statement in a READ transaction with timeout, row cap and size cap."""
        timeout_s = timeout_s or self.settings.query_timeout_s
        max_rows = max_rows or self.settings.query_max_rows
        if not self._slots.acquire(timeout=timeout_s):
            raise GraphBusyError("Too many concurrent graph queries; try again", code="Platform.Busy")
        started = time.perf_counter()
        try:
            def work(tx: neo4j.ManagedTransaction) -> tuple[list[str], list[dict[str, Any]], bool]:
                result = tx.run(cypher, params or {})
                columns = list(result.keys())
                rows: list[dict[str, Any]] = []
                truncated = False
                for record in result:
                    if len(rows) >= max_rows:
                        truncated = True
                        break
                    rows.append(dict(record.items()))
                result.consume()
                return columns, rows, truncated

            with self.driver.session(database=self.database, default_access_mode=neo4j.READ_ACCESS) as session:
                columns, raw_rows, truncated = session.execute_read(
                    neo4j.unit_of_work(timeout=timeout_s)(work))
        except neo4j.exceptions.Neo4jError as exc:
            raise GraphQueryError(exc.message or str(exc), code=exc.code) from exc
        except neo4j.exceptions.DriverError as exc:
            raise GraphQueryError(str(exc), code=type(exc).__name__) from exc
        finally:
            self._slots.release()

        evidence: set[str] = set()
        rows = []
        size = 0
        for raw in raw_rows:
            row = {k: _serialize(v, evidence) for k, v in raw.items()}
            size += len(json.dumps(row, default=str))
            if size > self.settings.query_max_result_bytes:
                truncated = True
                break
            _collect_id_columns(row, evidence)
            rows.append(row)
        return QueryResult(columns=columns, rows=rows, row_count=len(rows), truncated=truncated,
                           duration_ms=(time.perf_counter() - started) * 1000,
                           evidence_ids=sorted(evidence))

    def explain(self, cypher: str, params: dict[str, Any] | None = None) -> ExplainResult:
        """Ask the server to plan (not run) the statement: query type plus unknown-schema warnings."""
        try:
            with self.driver.session(database=self.database, default_access_mode=neo4j.READ_ACCESS) as session:
                summary = session.run("EXPLAIN " + cypher, params or {}).consume()
        except neo4j.exceptions.Neo4jError as exc:
            return ExplainResult(ok=False, error_code=exc.code, error_message=exc.message)
        except neo4j.exceptions.DriverError as exc:
            return ExplainResult(ok=False, error_code=type(exc).__name__, error_message=str(exc))
        result = ExplainResult(ok=True, query_type=summary.query_type)
        for status in summary.gql_status_objects:
            names = _UNKNOWN_NAME.findall(status.status_description or "")
            if status.gql_status == "01N50":
                result.unknown_labels.extend(names[:1])
            elif status.gql_status == "01N51":
                result.unknown_relationship_types.extend(names[:1])
            elif status.gql_status == "01N52":
                result.unknown_properties.extend(names[:1])
        return result
