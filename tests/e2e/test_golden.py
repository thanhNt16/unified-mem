"""M5 T2: golden corpus schema, ontology compliance, deterministic gate shape."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from kg.config import Config
from kg.dedup import Deduper
from kg.embed import FakeEmbedder
from kg.gate import Gate
from kg.ontology import (
    ALLOWED_NODE_TYPES,
    ALLOWED_SEMANTIC_EDGE_TYPES,
    STRUCTURAL_EDGE_TYPES,
)
from kg.resolve import Resolver
from kg.storage.sqlite import SQLiteAdapter

FIX = Path(__file__).parent / "fixtures"


# ---------- helpers ----------

def _load(name: str) -> dict:
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def _gate(tmp_path) -> tuple[SQLiteAdapter, Gate]:
    cfg = Config.default()
    a = SQLiteAdapter(tmp_path / "kg.db")
    g = Gate(
        a,
        Resolver(a, FakeEmbedder(), cfg.thresholds),
        Deduper(a, FakeEmbedder(), cfg.thresholds),
        FakeEmbedder(),
        cfg,
        user_id=cfg.project.user_id,
    )
    return a, g


# ---------- golden.json schema ----------

def test_golden_is_well_formed():
    g = _load("golden.json")
    for band in ("cli_deterministic", "llm_band"):
        assert band in g, f"missing band {band}"

    det = g["cli_deterministic"]
    assert isinstance(det["nodes_total"], int) and det["nodes_total"] >= 8
    assert isinstance(det["edges_total"], int) and det["edges_total"] >= 1
    assert sum(det["nodes_by_type"].values()) == det["nodes_total"]
    assert sum(det["edges_by_semantic_type"].values()) == det["edges_total"]
    for t in det["nodes_by_type"]:
        assert t in ALLOWED_NODE_TYPES, f"unknown node type {t}"
    for s in det["edges_by_semantic_type"]:
        assert s in (ALLOWED_SEMANTIC_EDGE_TYPES | STRUCTURAL_EDGE_TYPES), (
            f"unknown edge type {s}"
        )
    assert det["decisions"]["NEW"] + det["decisions"]["MERGED"] + \
        det["decisions"]["FLAGGED"] == det["nodes_total"]

    band = g["llm_band"]
    assert band["min_nodes"] >= 1
    assert band["min_edges"] >= 1
    assert isinstance(band["must_contain_entities"], list) and band["must_contain_entities"]
    assert len(band["gray_zone_pair"]) == 2


# ---------- extracted.json ontology compliance ----------

def test_extracted_matches_ontology():
    ex = _load("extracted.json")
    names = {n["name"] for n in ex["nodes"]}

    for n in ex["nodes"]:
        assert n["type"] in ALLOWED_NODE_TYPES, f"bad node type {n['type']}"

    valid_sem = ALLOWED_SEMANTIC_EDGE_TYPES | STRUCTURAL_EDGE_TYPES
    for e in ex["edges"]:
        assert e["semantic_type"] in valid_sem, f"bad edge type {e['semantic_type']}"
        assert e["source_name"] in names, (
            f"edge source {e['source_name']!r} not in nodes")
        assert e["target_name"] in names, (
            f"edge target {e['target_name']!r} not in nodes")

    for f in ex.get("facts", []):
        for k in ("subject", "predicate", "object"):
            assert k in f, f"fact missing {k}"

    for p in ex.get("preferences", []):
        assert p.get("name") or p.get("subject"), "preference needs name/subject"


def test_extracted_cross_doc_and_gray_zone():
    ex = _load("extracted.json")
    g = _load("golden.json")
    # Cross-document entity (Demis Hassabis) referenced via edges targeting org.
    names = {n["name"] for n in ex["nodes"]}
    assert g["llm_band"]["cross_doc_entity"] in names
    # Gray-zone pair both present.
    for alias in g["llm_band"]["gray_zone_pair"]:
        assert alias in names, f"gray-zone alias {alias!r} missing"


# ---------- cli_deterministic shape achievable via Gate + FakeEmbedder ----------

def test_cli_deterministic_shape(tmp_path):
    """Gate over extracted.json (FakeEmbedder, no LLM) must match golden.json band."""
    ex = _load("extracted.json")
    expected = _load("golden.json")["cli_deterministic"]
    a, g = _gate(tmp_path)

    report = g.normalize(
        ex["nodes"],
        ex["edges"],
        source="raw/corpus/note.md#chunk-0",
        facts=ex.get("facts"),
        preferences=ex.get("preferences"),
    )

    counts = a.count()
    assert counts["nodes"] == expected["nodes_total"], (
        f"nodes {counts['nodes']} != expected {expected['nodes_total']}")
    assert counts["edges"] == expected["edges_total"], (
        f"edges {counts['edges']} != expected {expected['edges_total']}")

    rows = a.conn.execute(
        "SELECT data FROM nodes ORDER BY id"
    ).fetchall()
    type_counts: dict[str, int] = {}
    canonical: set[str] = set()
    for r in rows:
        d = json.loads(r["data"])
        type_counts[d["type"]] = type_counts.get(d["type"], 0) + 1
        canonical.add(d["name"])
    assert type_counts == expected["nodes_by_type"], (
        f"node type distribution {type_counts} != {expected['nodes_by_type']}")
    for ent in expected["canonical_entities"]:
        assert ent in canonical, f"canonical entity {ent!r} missing from graph"

    edge_rows = a.conn.execute(
        "SELECT data FROM edges ORDER BY id"
    ).fetchall()
    sem_counts: dict[str, int] = {}
    for r in edge_rows:
        d = json.loads(r["data"])
        sem_counts[d["semantic_type"]] = sem_counts.get(d["semantic_type"], 0) + 1
    assert sem_counts == expected["edges_by_semantic_type"], (
        f"edge type distribution {sem_counts} != {expected['edges_by_semantic_type']}")

    decisions = {"NEW": 0, "MERGED": 0, "FLAGGED": 0}
    for d in report.decisions:
        decisions[d.action] += 1
    assert decisions == expected["decisions"], (
        f"decisions {decisions} != {expected['decisions']}")

    assert report.edges_upserted == expected["edges_total"]
    assert report.new_same_as == 0


def test_no_network_or_llm_in_assertions(tmp_path, monkeypatch):
    """Guardrail: deterministic path must never touch network or external LLM.

    Stub socket + ensure no ANTHROPIC_API_KEY leaks into env.
    """
    import os
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    import socket
    def _blocked(*a, **kw):
        raise AssertionError(
            "deterministic gate path must not open a socket")
    monkeypatch.setattr(socket, "socket", _blocked)

    ex = _load("extracted.json")
    _, g = _gate(tmp_path)
    g.normalize(
        ex["nodes"], ex["edges"],
        source="raw/corpus/note.md#chunk-0",
        facts=ex.get("facts"),
        preferences=ex.get("preferences"),
    )
