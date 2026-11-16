"""Regenerate the generated sections of docs/schema-mapping.md from the live graph and the installed Cartography.

    docker compose run --rm -v "$PWD/scripts:/scripts:ro" -v "$PWD/docs:/docs" graph-ingestion \
        python /scripts/generate_schema_mapping.py /docs/schema-mapping.md

Writes everything between the GENERATED markers; hand-written sections outside them are preserved.
"""

from __future__ import annotations

import dataclasses
import importlib
import importlib.resources
import inspect
import os
import pkgutil
import re
import sys
from collections import defaultdict
from pathlib import Path

from importlib.metadata import version as _pkg_version
import neo4j
import yaml
from cartography.models.core.nodes import CartographyNodeSchema
from cartography.models.core.relationships import LinkDirection
from cartography.rules.data.rules import RULES
from cartography.rules.spec.model import Module

START, END = "<!-- GENERATED:START -->", "<!-- GENERATED:END -->"


def _secret(name: str) -> str:
    path = os.environ.get(f"{name}_FILE")
    if path and os.path.exists(path):
        return Path(path).read_text().strip()
    return os.environ.get(name, "")


def declared():
    import cartography.models as models

    schemas = defaultdict(list)  # label -> [(module, schema_class, schema)]
    for info in pkgutil.walk_packages(models.__path__, models.__name__ + "."):
        try:
            mod = importlib.import_module(info.name)
        except Exception:  # noqa: BLE001
            continue
        for obj in vars(mod).values():
            if inspect.isclass(obj) and issubclass(obj, CartographyNodeSchema) and obj is not CartographyNodeSchema \
                    and obj.__module__ == mod.__name__:
                try:
                    s = obj()
                except TypeError:
                    continue
                schemas[s.label].append((mod.__name__, obj.__name__, s))
                for extra in (s.extra_node_labels.labels if s.extra_node_labels else []):
                    schemas[getattr(extra, "label", extra)].append((mod.__name__, obj.__name__ + " (extra label)", s))
    return schemas


def rel_sources(schemas):
    out = defaultdict(set)  # (src, type, dst) -> {module.Class}
    for label, entries in schemas.items():
        for mod, cls, s in entries:
            if "(extra label)" in cls:
                continue
            rels = ([s.sub_resource_relationship] if s.sub_resource_relationship else []) + \
                (list(s.other_relationships.rels) if s.other_relationships else [])
            for r in rels:
                key = (s.label, r.rel_label, r.target_node_label) if r.direction == LinkDirection.OUTWARD \
                    else (r.target_node_label, r.rel_label, s.label)
                out[key].add(f"{mod}.{cls}")
    perm = importlib.resources.files("cartography") / "data" / "gcp_permission_relationships.yaml"
    for e in yaml.safe_load(perm.read_text()):
        out[("GCPPrincipal", e["relationship_name"], e["target_label"])].add(
            "cartography/data/gcp_permission_relationships.yaml (GCPPermissionMatchLink)")
    from cartography.models.gcp.resource_catalog import GCP_FULL_NAME_MAPPINGS

    for m in GCP_FULL_NAME_MAPPINGS:
        out[("GCPPolicyBinding", "APPLIES_TO", m.label)].add(
            "cartography.models.gcp.policy_bindings.GCPPolicyBindingAppliesToMatchLink")
    from cartography.analysis.gcp import analysis

    src = inspect.getsource(analysis)
    for m in re.finditer(r'AddRelationship\(\s*"\w+",\s*"([A-Z_]+)",\s*"\w+",[^)]*?source_label="(\w+)",'
                         r'\s*target_label="(\w+)"', src, re.S):
        out[(m.group(2), m.group(1), m.group(3))].add("cartography.analysis.gcp.analysis (analysis job)")
    return out


def main(target: Path) -> None:
    driver = neo4j.GraphDatabase.driver(os.environ.get("NEO4J_URI", "bolt://localhost:7687"),
                                        auth=(os.environ.get("NEO4J_USER", "neo4j"), _secret("NEO4J_PASSWORD")))
    q = lambda c: [r.data() for r in driver.session().run(c)]  # noqa: E731
    schemas = declared()
    rels = rel_sources(schemas)
    label_counts = {r["l"]: r["c"] for r in q("MATCH (n) UNWIND labels(n) AS l RETURN l, count(*) AS c")}
    primary = lambda ls: _primary(schemas, ls)  # noqa: E731
    label_sets = sorted(q("MATCH (n) RETURN labels(n) AS ls, count(*) AS c, min(n.id) AS example"),
                        key=lambda r: primary(r["ls"]))
    for row in label_sets:
        row["ls"] = [primary(row["ls"])] + sorted(x for x in row["ls"] if x != primary(row["ls"]))
    props = defaultdict(set)
    for r in q("MATCH (n) UNWIND keys(n) AS k RETURN DISTINCT labels(n) AS ls, k"):
        props[primary(r["ls"])].add(r["k"])
    edge_counts = defaultdict(int)
    for r in q("MATCH (a)-[r]->(b) RETURN labels(a) AS a, type(r) AS t, labels(b) AS b, count(*) AS c"):
        edge_counts[(primary(r["a"]), r["t"], primary(r["b"]))] += r["c"]
    edges = [{"a": a, "t": t, "b": b, "c": c} for (a, t, b), c in sorted(edge_counts.items())]

    lines = [START, "", f"Generated from the live graph and the installed `cartography=={_pkg_version("cartography")}` "
             "package by `scripts/generate_schema_mapping.py`.", "",
             "### Node labels", "",
             "| Label set (primary first) | Nodes | Declared by (upstream schema class) | Example `id` |",
             "|---|---|---|---|"]
    for row in label_sets:
        main_label = row["ls"][0]
        decl = "; ".join(sorted({f"`{m}.{c}`" for m, c, _ in schemas.get(main_label, []) if "(extra" not in c})) or "-"
        lines.append(f"| {', '.join(f'`{x}`' for x in row['ls'])} | {row['c']} | {decl} | `{row['example']}` |")
    lines += ["", "Every label in the graph, with its node count (a node can carry several labels):", ""]
    lines.append(", ".join(f"`{k}` ({v})" for k, v in sorted(label_counts.items())))

    lines += ["", "### Properties written, by primary label", "",
              "`property <- upstream field` is the `PropertyRef` in the Cartography node schema: the key of the "
              "transformed API payload the loader reads. `firstseen`, `lastupdated`, `_module_name`, "
              "`_module_version` and `_ont_*` are written by Cartography's loader and ontology layer.", ""]
    for label in sorted(props):
        refs = {}
        for _, cls, s in schemas.get(label, []):
            for f in dataclasses.fields(s.properties):
                refs.setdefault(f.name, getattr(s.properties, f.name).name)
        cells = []
        for p in sorted(props[label]):
            src = refs.get(p)
            cells.append(f"`{p}`" + (f" <- `{src}`" if src and src != p else ""))
        lines.append(f"- **`{label}`**: " + ", ".join(cells))

    lines += ["", "### Relationships (graph direction)", "",
              "| Pattern | Count | Declared by |", "|---|---|---|"]
    for e in edges:
        candidates = [k for k in rels if k[1] == e["t"] and k[0] in _labels_of(schemas, e["a"])
                      and k[2] in _labels_of(schemas, e["b"])]
        decl = "; ".join(sorted({s for k in candidates for s in rels[k]})) or "**not declared**"
        lines.append(f"| `(:{e['a']})-[:{e['t']}]->(:{e['b']})` | {e['c']} | {decl} |")

    lines += ["", "### Cartography rules inventory (GCP facts)", "",
              "| Rule | Fact | Maturity | Requires (labels) | Graph has data? |", "|---|---|---|---|---|"]
    for rule in sorted(RULES.values(), key=lambda r: r.id):
        for fact in rule.facts:
            if fact.module != Module.GCP:
                continue
            needed = sorted(set(re.findall(r"\(\s*\w*\s*:\s*`?([A-Za-z]\w*)`?", fact.cypher_query)))
            missing = [n for n in needed if not label_counts.get(n)]
            status = "yes" if not missing else "no: missing " + ", ".join(f"`{m}`" for m in missing)
            lines.append(f"| `{rule.id}` | `{fact.id}` | {fact.maturity.value} | "
                         f"{', '.join(f'`{n}`' for n in needed)} | {status} |")
    lines += ["", END]

    text = target.read_text() if target.exists() else f"# Schema mapping\n\n{START}\n{END}\n"
    head, _, rest = text.partition(START)
    _, _, tail = rest.partition(END)
    target.write_text(head + "\n".join(lines) + tail)
    driver.close()
    print(f"Wrote {target}")


def _primary(schemas, labels: list[str]) -> str:
    """The schema label whose extra labels cover the node's other labels."""
    for candidate in sorted(labels):
        for _, cls, s in schemas.get(candidate, []):
            if "(extra label)" in cls or s.label != candidate:
                continue
            extras = {getattr(x, "label", x) for x in (s.extra_node_labels.labels if s.extra_node_labels else [])}
            if set(labels) - {candidate} <= extras:
                return candidate
    gcp = sorted(lbl for lbl in labels if lbl.startswith("GCP"))
    return gcp[0] if gcp else sorted(labels)[0]


def _labels_of(schemas, primary: str) -> set[str]:
    out = {primary}
    for _, cls, s in schemas.get(primary, []):
        out.add(s.label)
        out.update(getattr(x, "label", x) for x in (s.extra_node_labels.labels if s.extra_node_labels else []))
    return out


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "docs/schema-mapping.md"))
