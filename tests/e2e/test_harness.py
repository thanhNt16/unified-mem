"""Tests for E2E harness library: assert_graph_shape, kg_tool_json_equal, build_clean_env, SessionDriver ABC."""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from kg.ontology import Node, Edge
from kg.storage.sqlite import SQLiteAdapter
from tests.e2e.assertions import (
    assert_graph_shape,
    kg_tool_json_equal,
    GraphShape,
    ToolJsonMismatch,
)
from tests.e2e.harness import (
    SessionDriver,
    build_clean_env,
    CleanEnvError,
)


# ── helpers ─────────────────────────────────────────────────────────────

def _seed_db(path: Path, nodes: list[Node], edges: list[Edge] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    a = SQLiteAdapter(path)
    a.upsert_nodes(nodes)
    if edges:
        a.upsert_edges(edges)


GOOD_NODES = [
    Node(id="u:person:alice", type="person", name="Alice"),
    Node(id="u:organization:acme", type="organization", name="Acme"),
    Node(id="u:fact:x", type="fact", name="x is 42"),
]

GOOD_EDGES = [
    Edge(id="u:person:alice|employed_by|u:organization:acme",
         semantic_type="employed_by"),
]

BAD_TYPE_NODE = Node(id="u:goblin:zog", type="goblin", name="Zog")

BAD_TYPE_EDGE = Edge(id="u:person:alice|zaps|u:organization:acme",
                      semantic_type="zaps")


# ── assert_graph_shape ─────────────────────────────────────────────────

class TestAssertGraphShape:
    def test_in_band_passes(self, tmp_path):
        kg = tmp_path / "project" / ".kg"
        kg.mkdir(parents=True)
        _seed_db(kg / "kg.db", GOOD_NODES, GOOD_EDGES)
        shape = assert_graph_shape(
            tmp_path / "project", nodes=(3, 3), edges=(1, 1),
        )
        assert shape.node_count == 3
        assert shape.edge_count == 1
        assert shape.active_nodes == 3
        assert shape.tombstoned_nodes == 0
        assert shape.active_edges == 1
        assert shape.node_types == {"person": 1, "organization": 1, "fact": 1}
        assert shape.invalid_node_ids == []
        assert shape.invalid_edge_ids == []

    def test_unbounded_band(self, tmp_path):
        kg = tmp_path / "project" / ".kg"
        kg.mkdir(parents=True)
        _seed_db(kg / "kg.db", GOOD_NODES)
        shape = assert_graph_shape(tmp_path / "project")  # no bands
        assert shape.node_count == 3

    def test_too_few_nodes_fails(self, tmp_path):
        kg = tmp_path / "project" / ".kg"
        kg.mkdir(parents=True)
        _seed_db(kg / "kg.db", GOOD_NODES)
        with pytest.raises(AssertionError, match="nodes count 3 < min 5"):
            assert_graph_shape(tmp_path / "project", nodes=(5, None))

    def test_too_many_nodes_fails(self, tmp_path):
        kg = tmp_path / "project" / ".kg"
        kg.mkdir(parents=True)
        _seed_db(kg / "kg.db", GOOD_NODES)
        with pytest.raises(AssertionError, match="nodes count 3 > max 2"):
            assert_graph_shape(tmp_path / "project", nodes=(None, 2))

    def test_bad_node_type_fails(self, tmp_path):
        kg = tmp_path / "project" / ".kg"
        kg.mkdir(parents=True)
        _seed_db(kg / "kg.db", GOOD_NODES + [BAD_TYPE_NODE], GOOD_EDGES)
        with pytest.raises(AssertionError, match="nodes with disallowed type"):
            assert_graph_shape(tmp_path / "project")

    def test_bad_edge_type_fails(self, tmp_path):
        kg = tmp_path / "project" / ".kg"
        kg.mkdir(parents=True)
        _seed_db(kg / "kg.db", GOOD_NODES, [BAD_TYPE_EDGE])
        with pytest.raises(AssertionError, match="edges with disallowed semantic_type"):
            assert_graph_shape(tmp_path / "project")

    def test_missing_db_fails(self, tmp_path):
        with pytest.raises(AssertionError, match="no kg.db"):
            assert_graph_shape(tmp_path)

    def test_tombstone_counts(self, tmp_path):
        kg = tmp_path / "project" / ".kg"
        kg.mkdir(parents=True)
        db = kg / "kg.db"
        a = SQLiteAdapter(db)
        a.upsert_nodes(GOOD_NODES)
        a.delete("u:person:alice")
        shape = assert_graph_shape(tmp_path / "project")
        assert shape.tombstoned_nodes == 1
        assert shape.active_nodes == 2
        assert shape.node_count == 3


# ── kg_tool_json_equal ──────────────────────────────────────────────────

class TestKgToolJsonEqual:
    def test_identical(self):
        d = [{"id": "n1", "type": "person", "name": "Alice"}]
        assert kg_tool_json_equal(d, json.loads(json.dumps(d))) is True

    def test_order_independent_nodes(self):
        a = [{"id": "n1", "name": "A"}, {"id": "n2", "name": "B"}]
        b = [{"id": "n2", "name": "B"}, {"id": "n1", "name": "A"}]
        assert kg_tool_json_equal(a, b)

    def test_ignores_unstable_fields(self):
        a = [{"id": "n1", "type": "person", "created_at": "t1"}]
        b = [{"id": "n1", "type": "person", "created_at": "t2"}]
        assert kg_tool_json_equal(a, b)

    def test_missing_stable_field_fails(self):
        a = [{"id": "n1", "type": "person"}]
        b = [{"id": "n1"}]
        with pytest.raises(ToolJsonMismatch, match="key mismatch"):
            kg_tool_json_equal(a, b)

    def test_extra_id_fails(self):
        a = [{"id": "n1"}, {"id": "n2"}]
        b = [{"id": "n1"}, {"id": "n2"}, {"id": "n3"}]
        with pytest.raises(ToolJsonMismatch, match="id-set mismatch"):
            kg_tool_json_equal(a, b)

    def test_edge_order_independent(self):
        a = [{"source": "a", "target": "b", "type": "x"}]
        b = [{"source": "a", "target": "b", "type": "x"}]
        assert kg_tool_json_equal(a, b)

    def test_string_json_coercion(self):
        raw = json.dumps([{"id": "n1"}])
        assert kg_tool_json_equal(raw, [{"id": "n1"}])

    def test_primitive_lists_sorted(self):
        assert kg_tool_json_equal([3, 1, 2], [2, 1, 3])

    def test_primitive_mismatch(self):
        with pytest.raises(ToolJsonMismatch, match="1 != 2"):
            kg_tool_json_equal([1], [2])


# ── build_clean_env ────────────────────────────────────────────────────

class TestBuildCleanEnv:
    def test_has_path_and_home(self, tmp_path):
        env = build_clean_env(tmp_path)
        assert "PATH" in env
        assert env["HOME"] == str(tmp_path)

    def test_no_api_key_by_default(self, tmp_path):
        env = build_clean_env(tmp_path)
        assert "ANTHROPIC_API_KEY" not in env

    def test_api_key_present(self, tmp_path):
        env = build_clean_env(tmp_path, api_key="sk-test")
        assert env["ANTHROPIC_API_KEY"] == "sk-test"

    def test_empty_key_raises(self, tmp_path):
        with pytest.raises(CleanEnvError, match="non-empty"):
            build_clean_env(tmp_path, api_key="  ")

    def test_no_inherited_env_leaks(self, tmp_path):
        os.environ["_E2E_TEST_ONLY"] = "should-not-appear"
        try:
            env = build_clean_env(tmp_path)
            assert "_E2E_TEST_ONLY" not in env
        finally:
            del os.environ["_E2E_TEST_ONLY"]

    def test_extra_path_prepended(self, tmp_path):
        env = build_clean_env(tmp_path, extra_path=[tmp_path / "bin"])
        assert env["PATH"].startswith(str(tmp_path / "bin"))

    def test_creates_home_root(self, tmp_path):
        sub = tmp_path / "new_home"
        build_clean_env(sub)
        assert sub.is_dir()


# ── SessionDriver ABC (smoke) ──────────────────────────────────────────

class TestSessionDriverABC:
    def test_context_manager(self, tmp_path):
        """Concrete driver (stub) exercises context manager lifecycle."""

        class StubDriver(SessionDriver):
            called_start = False
            called_stop = False

            def _spawn(self, env):
                self.called_start = True
                proc = MagicMock()
                proc.poll.return_value = None
                proc.pid = 99999
                return proc

            def _send(self, message):
                return f"echo:{message}"

            def stop(self):
                super().stop()
                self.called_stop = True

        drv = StubDriver(
            project_root=tmp_path,
            home_root=tmp_path / "home",
            timeout=5,
        )
        with drv:
            assert drv.called_start
            out = drv.send("hello")
            assert out == "echo:hello"
        assert drv.called_stop
        assert drv._proc is None

    def test_double_start_raises(self, tmp_path):
        class NopDriver(SessionDriver):
            def _spawn(self, env): return MagicMock()
            def _send(self, msg): return ""

        drv = NopDriver(project_root=tmp_path, home_root=tmp_path)
        drv._proc = MagicMock()
        with pytest.raises(RuntimeError, match="already started"):
            drv.start()

    def test_send_without_start_raises(self, tmp_path):
        class NopDriver(SessionDriver):
            def _spawn(self, env): return MagicMock()
            def _send(self, msg): return ""

        drv = NopDriver(project_root=tmp_path, home_root=tmp_path)
        with pytest.raises(RuntimeError, match="not started"):
            drv.send("hello")

    def test_wait_for_timeout(self, tmp_path):
        class NopDriver(SessionDriver):
            def _spawn(self, env): return MagicMock()
            def _send(self, msg): return ""

        drv = NopDriver(project_root=tmp_path, home_root=tmp_path)
        drv._proc = MagicMock()  # pretend started
        with pytest.raises(TimeoutError, match="wait_for gave up"):
            drv.wait_for(lambda: False, timeout=0.3)
