"""Graph bootstrap: wait for Neo4j and create the dedicated read-only agent user.

Neo4j Community Edition has no role-based access control, so the agent user
cannot be given a read-only *role*. Read-only enforcement on the server comes
from the access mode of every transaction the API opens (READ), which Neo4j
refuses to upgrade to a write. See docs/decisions.md (D-04) and
docs/limitations.md.
"""

from __future__ import annotations

import logging
import time

import neo4j

logger = logging.getLogger(__name__)


def wait_for_neo4j(uri: str, auth: tuple[str, str], timeout_s: int = 180) -> neo4j.Driver:
    deadline = time.monotonic() + timeout_s
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        driver = neo4j.GraphDatabase.driver(uri, auth=auth)
        try:
            driver.verify_connectivity()
            return driver
        except Exception as exc:  # noqa: BLE001 - retry any startup error
            last_error = exc
            driver.close()
            time.sleep(3)
    raise RuntimeError(f"Neo4j not reachable at {uri} after {timeout_s}s: {last_error}")


def ensure_reader_user(driver: neo4j.Driver, username: str, password: str) -> None:
    """Create (or reset the password of) the agent's dedicated user. Idempotent."""
    if not username or not password:
        raise ValueError("NEO4J_READER_USER and NEO4J_READER_PASSWORD must be set")
    with driver.session(database="system") as session:
        session.run(
            "CREATE USER $user IF NOT EXISTS SET PLAINTEXT PASSWORD $password CHANGE NOT REQUIRED",
            user=username, password=password,
        ).consume()
        try:
            # Keep the stored password in sync with the environment after a credential change.
            session.run(
                "ALTER USER $user SET PLAINTEXT PASSWORD $password CHANGE NOT REQUIRED",
                user=username, password=password,
            ).consume()
        except neo4j.exceptions.ClientError as exc:
            if "cannot be the same" not in (exc.message or ""):
                raise
    logger.info("Read-only agent user '%s' is present", username)
