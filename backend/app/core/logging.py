"""Logging setup and the query audit log."""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def configure_logging() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    # The driver logs every schema notification from EXPLAIN at WARNING; we consume them ourselves.
    logging.getLogger("neo4j").setLevel(logging.ERROR)
    logging.getLogger("neo4j.notifications").setLevel(logging.ERROR)
    logging.getLogger("httpx").setLevel(logging.WARNING)


class AuditLog:
    """Append-only JSON-lines log of every query the platform considers running.

    Records the question, the Cypher, the validation verdict, the execution outcome,
    duration and row count. Credentials are never passed to it.
    """

    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        self._logger = logging.getLogger("audit")
        self.recent: list[dict[str, Any]] = []
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            self._logger.warning("Audit log directory %s not writable; logging to stdout only", self.path.parent)

    def record(self, **entry: Any) -> dict[str, Any]:
        entry = {"timestamp": datetime.now(timezone.utc).isoformat(), **entry}
        line = json.dumps(entry, default=str)
        with self._lock:
            self.recent.append(entry)
            del self.recent[:-200]
            try:
                with self.path.open("a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
            except OSError:
                pass
        self._logger.info(line)
        return entry
