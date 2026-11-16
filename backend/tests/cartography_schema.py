"""Collect what the *installed* Cartography package declares, for schema-conformance tests.

Sources: every CartographyNodeSchema in cartography.models (labels, extra labels,
properties, relationships with direction), every CartographyRelSchema subclass
(MatchLinks such as APPLIES_TO), the GCP permission-relationship mapping file
(CAN_READ / CAN_WRITE / ...) and the GCP analysis jobs.
"""

from __future__ import annotations

import dataclasses
import importlib
import importlib.resources
import inspect
import pkgutil
import re
from dataclasses import dataclass, field
from functools import lru_cache

import yaml
from cartography.models.core.nodes import CartographyNodeSchema
from cartography.models.core.relationships import CartographyRelSchema, LinkDirection

# Properties Cartography's loader writes on every node regardless of schema.
CARTOGRAPHY_MANAGED_PROPERTIES = {"id", "lastupdated", "firstseen", "_module_name", "_module_version"}
ONTOLOGY_PROPERTY_PREFIX = "_ont_"


@dataclass
class DeclaredSchema:
    labels: set[str] = field(default_factory=set)
    properties_by_label: dict[str, set[str]] = field(default_factory=dict)
    # (source_label, rel_type, target_label) in graph direction
    patterns: set[tuple[str, str, str]] = field(default_factory=set)
    rel_types: set[str] = field(default_factory=set)
    sources: dict[str, str] = field(default_factory=dict)


def _label(obj) -> str:
    return getattr(obj, "label", obj)


def _add_rel(declared: DeclaredSchema, node_label: str, rel: CartographyRelSchema) -> None:
    declared.rel_types.add(rel.rel_label)
    if rel.direction == LinkDirection.OUTWARD:
        declared.patterns.add((node_label, rel.rel_label, rel.target_node_label))
    else:
        declared.patterns.add((rel.target_node_label, rel.rel_label, node_label))


@lru_cache(maxsize=1)
def declared_schema() -> DeclaredSchema:
    import cartography.models as models

    declared = DeclaredSchema()
    for mod_info in pkgutil.walk_packages(models.__path__, models.__name__ + "."):
        try:
            module = importlib.import_module(mod_info.name)
        except Exception:  # noqa: BLE001 - optional provider deps
            continue
        for obj in vars(module).values():
            if not inspect.isclass(obj) or obj.__module__ != module.__name__:
                continue
            if issubclass(obj, CartographyNodeSchema) and obj is not CartographyNodeSchema:
                try:
                    schema = obj()
                except TypeError:
                    continue
                labels = [schema.label] + [_label(x) for x in (schema.extra_node_labels.labels
                                                                if schema.extra_node_labels else [])]
                props = {f.name for f in dataclasses.fields(schema.properties)}
                for lbl in labels:
                    declared.labels.add(lbl)
                    declared.properties_by_label.setdefault(lbl, set()).update(props)
                    declared.sources.setdefault(lbl, module.__name__)
                rels = ([schema.sub_resource_relationship] if schema.sub_resource_relationship else []) + \
                    (list(schema.other_relationships.rels) if schema.other_relationships else [])
                for rel in rels:
                    _add_rel(declared, schema.label, rel)
            elif issubclass(obj, CartographyRelSchema) and obj is not CartographyRelSchema:
                rel_label = getattr(obj, "rel_label", None)
                if isinstance(rel_label, str):
                    declared.rel_types.add(rel_label)

    # GCP permission relationships (MatchLinks whose type comes from data)
    mapping = importlib.resources.files("cartography") / "data" / "gcp_permission_relationships.yaml"
    for entry in yaml.safe_load(mapping.read_text()):
        declared.rel_types.add(entry["relationship_name"])
        declared.patterns.add(("GCPPrincipal", entry["relationship_name"], entry["target_label"]))
    # APPLIES_TO MatchLink: binding -> any resource in the GCP resource catalogue
    from cartography.models.gcp.resource_catalog import GCP_FULL_NAME_MAPPINGS

    for m in GCP_FULL_NAME_MAPPINGS:
        declared.patterns.add(("GCPPolicyBinding", "APPLIES_TO", m.label))
    declared.rel_types.add("APPLIES_TO")

    # Relationships created by GCP analysis jobs
    from cartography.analysis.gcp import analysis

    src = inspect.getsource(analysis)
    for match in re.finditer(r'AddRelationship\(\s*"\w+",\s*"([A-Z_]+)",\s*"\w+",[^)]*?source_label="(\w+)",'
                             r'\s*target_label="(\w+)"', src, re.S):
        declared.rel_types.add(match.group(1))
        declared.patterns.add((match.group(2), match.group(1), match.group(3)))
    return declared
