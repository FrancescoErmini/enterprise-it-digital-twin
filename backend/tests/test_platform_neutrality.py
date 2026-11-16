"""The platform core must not contain cloud-provider or security-domain vocabulary.

Scans every file of the core packages (core, technology_graph, it_intelligence_agent, api, models, the
use-case registry and the composition root) for forbidden identifiers.
"""

import re
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
CORE_PATHS = [APP / "core", APP / "technology_graph", APP / "it_intelligence_agent", APP / "api", APP / "models",
              APP / "usecases" / "registry.py", APP / "platform.py", APP / "main.py"]
FORBIDDEN = re.compile(
    r"(?i)(?<![a-z])(gcp|google|firewalls?|service_?accounts?|iam|buckets?|vpcs?|aws|azure|cloud_?sql|"
    r"compute_?engine|cve)(?![a-z])")


def _files():
    for p in CORE_PATHS:
        yield from ([p] if p.is_file() else sorted(p.rglob("*.py")))


def test_core_has_no_provider_or_security_vocabulary():
    hits = []
    for path in _files():
        for lineno, line in enumerate(path.read_text().splitlines(), start=1):
            for m in FORBIDDEN.finditer(line):
                hits.append(f"{path.relative_to(APP)}:{lineno}: {m.group(0)}")
    assert not hits, "Provider/security vocabulary in platform core:\n" + "\n".join(hits)


def test_scanner_catches_violations():
    for sample in ["gcp_client", "def list_buckets()", "Firewall", "service_account", "IAM policy", "my_iam"]:
        assert FORBIDDEN.search(sample), sample
    for sample in ["diameter", "williams", "streamline"]:
        assert not FORBIDDEN.search(sample), sample


def test_core_does_not_import_usecases():
    for path in _files():
        if path.name == "registry.py" and path.parent.name == "usecases":
            continue
        text = path.read_text()
        assert "app.usecases.security" not in text, path
