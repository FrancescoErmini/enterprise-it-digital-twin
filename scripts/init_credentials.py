"""Generate Neo4j credentials locally on first start (never committed, never in .env).

Writes into the `secrets` volume:
  neo4j_auth              "neo4j/<password>"  (consumed by the neo4j image via NEO4J_AUTH_FILE)
  neo4j_admin_password    admin password      (graph-ingestion only)
  neo4j_reader_password   read-only agent user password (graph-ingestion creates the user, the API uses it)
Existing files are kept, so restarts reuse the same credentials.
"""

import os
import secrets
from pathlib import Path

root = Path(os.environ.get("SECRETS_DIR", "/secrets"))
root.mkdir(parents=True, exist_ok=True)


def ensure(name: str, value: str) -> str:
    path = root / name
    if not path.exists():
        path.write_text(value)
    path.chmod(0o644)
    return path.read_text().strip()


admin = ensure("neo4j_admin_password", secrets.token_urlsafe(24))
ensure("neo4j_auth", f"neo4j/{admin}")
ensure("neo4j_reader_password", secrets.token_urlsafe(24))
print(f"Credentials present in {root} (values not printed)")
