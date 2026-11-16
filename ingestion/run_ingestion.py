"""graph-ingestion entrypoint.

    python run_ingestion.py                      # bootstrap + seed (idempotent)
    python run_ingestion.py --reset              # wipe the graph first, then seed
    python run_ingestion.py --verify-idempotent  # seed twice and compare counts (exit 1 on drift)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone

import providers.gcp.synthetic  # noqa: F401  (registers the gcp/synthetic data source)
from graph_ingestion import registry
from graph_ingestion.bootstrap import ensure_reader_user, wait_for_neo4j
from graph_ingestion.manifest import graph_counts, write_manifest

logger = logging.getLogger("graph-ingestion")


def _env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None:
        raise SystemExit(f"Missing required environment variable {name}")
    return value


def _secret(name: str) -> str:
    path = os.environ.get(f"{name}_FILE")
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            return fh.read().strip()
    return _env(name)


def seed_once(driver, source, database: str) -> dict:
    update_tag = int(time.time())
    with driver.session(database=database) as session:
        stats = source.loader(session, update_tag)
        counts = graph_counts(session)
    return {"update_tag": update_tag, "stats": stats, "counts": counts}


def main() -> int:
    parser = argparse.ArgumentParser(description="Populate the technology graph.")
    parser.add_argument("--provider", default=os.environ.get("INGESTION_PROVIDER", "gcp"))
    parser.add_argument("--mode", default=os.environ.get("DATA_SOURCE_MODE", "synthetic"))
    parser.add_argument("--reset", action="store_true", help="Delete every node before seeding")
    parser.add_argument("--verify-idempotent", action="store_true",
                        help="Seed twice and fail if node/relationship/index counts differ")
    args = parser.parse_args()

    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger("neo4j").setLevel(logging.ERROR)
    logging.getLogger("cartography").setLevel(logging.WARNING)

    uri = _env("NEO4J_URI", "bolt://localhost:7687")
    database = _env("NEO4J_DATABASE", "neo4j")
    driver = wait_for_neo4j(uri, (_env("NEO4J_USER", "neo4j"), _secret("NEO4J_PASSWORD")))
    ensure_reader_user(driver, _env("NEO4J_READER_USER", "twin_reader"), _secret("NEO4J_READER_PASSWORD"))

    source = registry.get(args.provider, args.mode)
    if args.reset:
        with driver.session(database=database) as session:
            session.run("MATCH (n) CALL (n) { DETACH DELETE n } IN TRANSACTIONS OF 5000 ROWS").consume()
        logger.info("Graph reset: all nodes removed")

    first = seed_once(driver, source, database)
    c = first["counts"]
    logger.info("Seed complete: %d nodes, %d relationships, %d indexes", c["nodes"], c["relationships"], c["indexes"])
    exit_code = 0

    if args.verify_idempotent:
        second = seed_once(driver, source, database)
        keys = ("nodes", "relationships", "indexes", "nodes_by_label", "relationships_by_type")
        drift = {k: (first["counts"][k], second["counts"][k]) for k in keys if first["counts"][k] != second["counts"][k]}
        report = {"run_1": {k: first["counts"][k] for k in ("nodes", "relationships", "indexes")},
                  "run_2": {k: second["counts"][k] for k in ("nodes", "relationships", "indexes")},
                  "identical": not drift}
        print(json.dumps(report, indent=2))
        if drift:
            logger.error("Idempotency check FAILED: %s", drift)
            exit_code = 1
        first = second

    write_manifest(_env("INGESTION_MANIFEST_PATH", "/data/manifest/ingestion.json"), {
        "provider_id": source.provider_id,
        "display_name": source.display_name,
        "ingestion_mode": source.ingestion_mode,
        "description": source.description,
        "root_label": source.root_label,
        "documented_modes": list(source.documented_modes),
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "update_tag": first["update_tag"],
        "counts": first["counts"],
    })
    driver.close()
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
