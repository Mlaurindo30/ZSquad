"""Testes TDD (Red-Green) para scripts/learning_graph.py e scripts/learning_mutations.py.

Garante 100% de statements e branches dos dois módulos novos:
    python -m pytest scripts/tests/test_learning_graph_coverage.py \
        --cov=scripts.learning_graph --cov=scripts.learning_mutations --cov-branch
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from scripts.learning_graph import (  # noqa: E402
    _load_catalog,
    _load_memory_deltas,
    _memory_memory_edges,
    _memory_skill_edges,
    _skill_skill_edges,
    build_learning_graph,
    density_stats,
)
from scripts.learning_mutations import (  # noqa: E402
    delete_node,
    edit_node,
    parse_node_kind,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_catalog(tmp_path: Path, entries: list) -> Path:
    path = tmp_path / "skills-catalog.yaml"
    path.write_text(yaml.safe_dump({"catalog": entries}, allow_unicode=True), encoding="utf-8")
    return path


def make_skill(name: str, domain: str = "", assigned_to=None, description: str = "") -> dict:
    return {
        "name": name,
        "path": f"skills/{name}",
        "domain": domain,
        "source": "user-local-curation",
        "assigned_to": assigned_to if assigned_to is not None else [],
        "description": description,
    }


def make_work_root(tmp_path: Path) -> Path:
    root = tmp_path / "work"
    root.mkdir()
    return root


def write_delta(
    work_root: Path,
    work_id: str,
    mem_name: str,
    statements: list,
    work_item_id: str = "WI-1",
) -> Path:
    folder = work_root / work_id / "memory" / "deltas"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{mem_name}.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "id": mem_name,
                "work_item_id": work_item_id,
                "author": "tester",
                "recorded_at": "2026-08-21T00:00:00Z",
                "entries": [
                    {
                        "kind": "fact",
                        "statement": s,
                        "source": "specs/x.md",
                        "confidence": "high",
                        "sensitivity": "internal",
                        "invalidates_when": None,
                    }
                    for s in statements
                ],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    return path


def edge_pairs(graph: dict) -> set:
    return {(e["source"], e["target"]) for e in graph["edges"]}


# ---------------------------------------------------------------------------
# _load_catalog
# ---------------------------------------------------------------------------


class TestLoadCatalog:
    def test_loads_skills_from_catalog(self, tmp_path):
        path = make_catalog(
            tmp_path,
            [
                make_skill("ai-engineer", domain="ai", assigned_to=["agent-a"], description="Alpha AI agents"),
                make_skill("langgraph", domain="ai", assigned_to=["agent-a", ""], description="Beta graphs"),
            ],
        )
        skills = _load_catalog(path)
        assert [s["id"] for s in skills] == ["ai-engineer", "langgraph"]
        first = skills[0]
        assert first["label"] == "ai-engineer"
        assert first["kind"] == "skill"
        assert first["domain"] == "ai"
        assert first["source"] == "user-local-curation"
        assert first["description"] == "Alpha AI agents"
        # string vazia em assigned_to é descartada
        assert skills[1]["assigned_to"] == ["agent-a"]

    def test_missing_file_returns_empty(self, tmp_path):
        assert _load_catalog(tmp_path / "nope.yaml") == []

    def test_invalid_yaml_returns_empty(self, tmp_path):
        path = tmp_path / "skills-catalog.yaml"
        path.write_text("catalog: [unclosed", encoding="utf-8")
        assert _load_catalog(path) == []

    def test_non_dict_document_returns_empty(self, tmp_path):
        path = tmp_path / "skills-catalog.yaml"
        path.write_text("- a\n- b\n", encoding="utf-8")
        assert _load_catalog(path) == []

    def test_missing_catalog_key_returns_empty(self, tmp_path):
        path = tmp_path / "skills-catalog.yaml"
        path.write_text(yaml.safe_dump({"version": 2}), encoding="utf-8")
        assert _load_catalog(path) == []

    def test_skips_invalid_and_duplicated_entries(self, tmp_path):
        path = make_catalog(
            tmp_path,
            ["junk", {"domain": "ai"}, {"name": "dup"}, {"name": "dup"}, make_skill("keep")],
        )
        skills = _load_catalog(path)
        # entradas não-dict e sem nome são puladas; primeira ocorrência de
        # "dup" é mantida e a duplicata é descartada
        assert [s["id"] for s in skills] == ["dup", "keep"]

    def test_tolerates_missing_fields(self, tmp_path):
        path = make_catalog(tmp_path, [{"name": "bare"}, {"name": "nullish", "assigned_to": None, "domain": None}])
        skills = _load_catalog(path)
        assert skills[0]["domain"] == ""
        assert skills[0]["assigned_to"] == []
        assert skills[1]["assigned_to"] == []
        assert skills[1]["domain"] == ""


# ---------------------------------------------------------------------------
# _load_memory_deltas
# ---------------------------------------------------------------------------


class TestLoadMemoryDeltas:
    def test_loads_memory_deltas(self, tmp_path):
        work = make_work_root(tmp_path)
        write_delta(work, "WI-1", "MEM-WI-1-001", ["usar o grafo"], work_item_id="WI-1")
        write_delta(work, "WI-2", "MEM-WI-2-001", ["outra decisão"], work_item_id="WI-2")
        memories = _load_memory_deltas(work)
        assert [m["id"] for m in memories] == ["memory:MEM-WI-1-001", "memory:MEM-WI-2-001"]
        first = memories[0]
        assert first["label"] == "MEM-WI-1-001"
        assert first["kind"] == "memory"
        assert first["work_item_id"] == "WI-1"
        assert first["author"] == "tester"
        assert first["recorded_at"] == "2026-08-21T00:00:00Z"
        assert first["statements"] == ["usar o grafo"]

    def test_missing_work_root_returns_empty(self, tmp_path):
        assert _load_memory_deltas(tmp_path / "missing") == []

    def test_invalid_yaml_skipped(self, tmp_path):
        work = make_work_root(tmp_path)
        bad = work / "WI-1" / "memory" / "deltas" / "MEM-BAD.yaml"
        bad.parent.mkdir(parents=True)
        bad.write_text("entries: [unclosed", encoding="utf-8")
        assert _load_memory_deltas(work) == []

    def test_non_dict_document_skipped(self, tmp_path):
        work = make_work_root(tmp_path)
        bad = work / "WI-1" / "memory" / "deltas" / "MEM-LIST.yaml"
        bad.parent.mkdir(parents=True)
        bad.write_text("- 1\n- 2\n", encoding="utf-8")
        assert _load_memory_deltas(work) == []

    def test_unreadable_entry_skipped(self, tmp_path):
        work = make_work_root(tmp_path)
        # um diretório com nome de delta: read_text levanta OSError
        (work / "WI-1" / "memory" / "deltas" / "MEM-DIR.yaml").mkdir(parents=True)
        write_delta(work, "WI-2", "MEM-WI-2-001", ["ok"])
        memories = _load_memory_deltas(work)
        assert [m["id"] for m in memories] == ["memory:MEM-WI-2-001"]

    def test_duplicate_stem_first_sorted_path_wins(self, tmp_path):
        work = make_work_root(tmp_path)
        write_delta(work, "WI-B", "MEM-SAME", ["b"], work_item_id="WI-B")
        write_delta(work, "WI-A", "MEM-SAME", ["a"], work_item_id="WI-A")
        memories = _load_memory_deltas(work)
        assert len(memories) == 1
        assert memories[0]["work_item_id"] == "WI-A"

    def test_entries_not_list_yields_no_statements(self, tmp_path):
        work = make_work_root(tmp_path)
        path = work / "WI-1" / "memory" / "deltas" / "MEM-STR.yaml"
        path.parent.mkdir(parents=True)
        path.write_text(yaml.safe_dump({"id": "MEM-STR", "work_item_id": "WI-1", "entries": "not-a-list"}), encoding="utf-8")
        memories = _load_memory_deltas(work)
        assert memories[0]["statements"] == []

    def test_skips_non_dict_entries_and_missing_statements(self, tmp_path):
        work = make_work_root(tmp_path)
        path = work / "WI-1" / "memory" / "deltas" / "MEM-MIX.yaml"
        path.parent.mkdir(parents=True)
        path.write_text(
            yaml.safe_dump({"id": "MEM-MIX", "work_item_id": "WI-1", "entries": ["junk", {"kind": "fact"}]}),
            encoding="utf-8",
        )
        memories = _load_memory_deltas(work)
        assert memories[0]["statements"] == [""]


# ---------------------------------------------------------------------------
# _skill_skill_edges
# ---------------------------------------------------------------------------


class TestSkillSkillEdges:
    def test_same_domain_creates_edge(self, tmp_path):
        skills = _load_catalog(
            make_catalog(tmp_path, [make_skill("a", domain="ai"), make_skill("b", domain="ai")])
        )
        assert _skill_skill_edges(skills) == [("a", "b")]

    def test_shared_agent_creates_edge(self, tmp_path):
        skills = _load_catalog(
            make_catalog(
                tmp_path,
                [make_skill("a", domain="ai", assigned_to=["x"]), make_skill("b", domain="dev", assigned_to=["x", "y"])],
            )
        )
        assert _skill_skill_edges(skills) == [("a", "b")]

    def test_unrelated_skills_have_no_edge(self, tmp_path):
        skills = _load_catalog(
            make_catalog(
                tmp_path,
                [make_skill("a", domain="ai", assigned_to=["x"]), make_skill("b", domain="dev", assigned_to=["y"])],
            )
        )
        assert _skill_skill_edges(skills) == []

    def test_empty_domains_do_not_link(self, tmp_path):
        skills = _load_catalog(make_catalog(tmp_path, [make_skill("a"), make_skill("b")]))
        assert _skill_skill_edges(skills) == []


# ---------------------------------------------------------------------------
# _memory_skill_edges
# ---------------------------------------------------------------------------


class TestMemorySkillEdges:
    def test_name_bonus_and_zero_score_skill(self, tmp_path):
        catalog = make_catalog(
            tmp_path,
            [
                make_skill("docker-deploy", description="containers production"),
                make_skill("poetry", description="dependency management"),
            ],
        )
        skills = _load_catalog(catalog)
        memories = [{"id": "memory:MEM-1", "statements": ["docker-deploy acelera o build"]}]
        edges = _memory_skill_edges(memories, skills)
        assert edges == [("memory:MEM-1", "docker-deploy")]

    def test_top_four_skills_kept(self, tmp_path):
        entries = [make_skill(f"cap-{s}", description="tokenizer pipeline shared") for s in "abcde"]
        skills = _load_catalog(make_catalog(tmp_path, entries))
        memories = [{"id": "memory:MEM-TOP", "statements": ["tokenizer pipeline shared flows"]}]
        edges = _memory_skill_edges(memories, skills)
        assert [target for _, target in edges] == ["cap-a", "cap-b", "cap-c", "cap-d"]

    def test_memory_without_statements_has_no_edges(self, tmp_path):
        skills = _load_catalog(make_catalog(tmp_path, [make_skill("a", description="pipeline dados")]))
        memories = [{"id": "memory:MEM-EMPTY", "statements": []}]
        assert _memory_skill_edges(memories, skills) == []

    def test_memory_matching_nothing_has_no_edges(self, tmp_path):
        skills = _load_catalog(make_catalog(tmp_path, [make_skill("a", description="pipeline dados")]))
        memories = [{"id": "memory:MEM-ZZZ", "statements": ["zzz qqq xxx"]}]
        assert _memory_skill_edges(memories, skills) == []


# ---------------------------------------------------------------------------
# _memory_memory_edges
# ---------------------------------------------------------------------------


class TestMemoryMemoryEdges:
    def test_same_work_item_creates_edge(self):
        memories = [
            {"id": "memory:MEM-1", "work_item_id": "WI-1"},
            {"id": "memory:MEM-2", "work_item_id": "WI-1"},
        ]
        assert _memory_memory_edges(memories) == [("memory:MEM-1", "memory:MEM-2")]

    def test_different_work_items_have_no_edge(self):
        memories = [
            {"id": "memory:MEM-1", "work_item_id": "WI-1"},
            {"id": "memory:MEM-2", "work_item_id": "WI-2"},
        ]
        assert _memory_memory_edges(memories) == []

    def test_empty_work_item_does_not_link(self):
        memories = [
            {"id": "memory:MEM-1", "work_item_id": ""},
            {"id": "memory:MEM-2", "work_item_id": ""},
        ]
        assert _memory_memory_edges(memories) == []


# ---------------------------------------------------------------------------
# density_stats
# ---------------------------------------------------------------------------


class TestDensityStats:
    def test_counts_nodes_edges_isolated_density(self):
        stats = density_stats(["a", "b", "c", "d"], [("a", "b"), ("b", "c")])
        assert stats == {"node_count": 4, "edge_count": 2, "isolated": 1, "density": 0.333}

    def test_single_node_density_zero(self):
        stats = density_stats(["only"], [])
        assert stats == {"node_count": 1, "edge_count": 0, "isolated": 1, "density": 0.0}

    def test_empty_graph(self):
        stats = density_stats([], [])
        assert stats == {"node_count": 0, "edge_count": 0, "isolated": 0, "density": 0.0}


# ---------------------------------------------------------------------------
# build_learning_graph (integração)
# ---------------------------------------------------------------------------


class TestBuildLearningGraph:
    def test_full_graph(self, tmp_path):
        catalog = make_catalog(
            tmp_path,
            [
                make_skill("skill-alpha", domain="ai", assigned_to=["agent-a"]),
                make_skill("skill-beta", domain="ai", assigned_to=["agent-b"]),
                make_skill("skill-gamma", domain="dev", assigned_to=["agent-b"]),
            ],
        )
        work = make_work_root(tmp_path)
        write_delta(work, "WI-1", "MEM-WI1-001", ["pipeline de dados com beta"], work_item_id="WI-1")
        write_delta(work, "WI-1", "MEM-WI1-002", ["decisao registrar pipeline"], work_item_id="WI-1")

        graph = build_learning_graph(catalog, work)

        assert [n["id"] for n in graph["nodes"]] == [
            "skill-alpha",
            "skill-beta",
            "skill-gamma",
            "memory:MEM-WI1-001",
            "memory:MEM-WI1-002",
        ]
        assert edge_pairs(graph) == {
            ("skill-alpha", "skill-beta"),
            ("skill-beta", "skill-gamma"),
            ("memory:MEM-WI1-001", "skill-beta"),
            ("memory:MEM-WI1-001", "memory:MEM-WI1-002"),
        }
        assert graph["stats"] == {"node_count": 5, "edge_count": 4, "isolated": 0, "density": 0.4}

    def test_missing_inputs_yield_empty_graph(self, tmp_path):
        graph = build_learning_graph(tmp_path / "missing.yaml", tmp_path / "missing-work")
        assert graph == {
            "nodes": [],
            "edges": [],
            "stats": {"node_count": 0, "edge_count": 0, "isolated": 0, "density": 0.0},
        }


# ---------------------------------------------------------------------------
# learning_mutations.parse_node_kind
# ---------------------------------------------------------------------------


class TestParseNodeKind:
    def test_memory_prefix(self):
        assert parse_node_kind("memory:MEM-X-001") == "memory"

    def test_bare_skill(self):
        assert parse_node_kind("docker-deploy") == "skill"


# ---------------------------------------------------------------------------
# learning_mutations.edit_node
# ---------------------------------------------------------------------------


def make_graph() -> dict:
    return {
        "nodes": [
            {"id": "skill-a", "kind": "skill", "domain": "ai", "state": "active"},
            {"id": "skill-b", "kind": "skill", "domain": "ai", "state": "active"},
            {"id": "memory:MEM-X-001", "kind": "memory", "work_item_id": "WI-1"},
        ],
        "edges": [
            {"source": "skill-a", "target": "skill-b"},
            {"source": "memory:MEM-X-001", "target": "skill-a"},
        ],
        "stats": {"node_count": 3, "edge_count": 2, "isolated": 0, "density": 0.667},
    }


class TestEditNode:
    def test_edit_writes_backup_then_updates_node(self, tmp_path):
        graph = make_graph()
        backup = tmp_path / "backup.json"
        result = edit_node(graph, "skill-a", {"state": "archived"}, backup)
        assert result["ok"] is True
        assert "updated" in result["message"]
        assert result["backup"] == str(backup)
        node = next(n for n in graph["nodes"] if n["id"] == "skill-a")
        assert node["state"] == "archived"
        # backup captura o estado ANTES da mutação
        saved = json.loads(backup.read_text(encoding="utf-8"))
        saved_node = next(n for n in saved["nodes"] if n["id"] == "skill-a")
        assert saved_node["state"] == "active"

    def test_edit_unknown_node_fails_without_backup(self, tmp_path):
        graph = make_graph()
        backup = tmp_path / "backup.json"
        result = edit_node(graph, "nope", {"state": "x"}, backup)
        assert result["ok"] is False
        assert "not found" in result["message"]
        assert not backup.exists()

    def test_edit_empty_graph_fails(self, tmp_path):
        result = edit_node({}, "x", {"a": 1}, tmp_path / "b.json")
        assert result["ok"] is False

    def test_edit_rejects_non_dict_patch(self, tmp_path):
        graph = make_graph()
        result = edit_node(graph, "skill-a", None, tmp_path / "b.json")
        assert result["ok"] is False
        assert "non-empty mapping" in result["message"]

    def test_edit_rejects_empty_patch(self, tmp_path):
        graph = make_graph()
        result = edit_node(graph, "skill-a", {}, tmp_path / "b.json")
        assert result["ok"] is False
        assert "non-empty mapping" in result["message"]

    def test_edit_rejects_id_change(self, tmp_path):
        graph = make_graph()
        result = edit_node(graph, "skill-a", {"id": "other"}, tmp_path / "b.json")
        assert result["ok"] is False
        assert "stable" in result["message"]

    def test_edit_accepts_patch_with_same_id(self, tmp_path):
        graph = make_graph()
        result = edit_node(graph, "skill-a", {"id": "skill-a", "state": "pinned"}, tmp_path / "b.json")
        assert result["ok"] is True
        node = next(n for n in graph["nodes"] if n["id"] == "skill-a")
        assert node["state"] == "pinned"

    def test_edit_backup_failure_leaves_graph_untouched(self, tmp_path):
        graph = make_graph()
        blocker = tmp_path / "blocker.txt"
        blocker.write_text("i am a file", encoding="utf-8")
        result = edit_node(graph, "skill-a", {"state": "x"}, blocker / "backup.json")
        assert result["ok"] is False
        assert "backup failed" in result["message"]
        node = next(n for n in graph["nodes"] if n["id"] == "skill-a")
        assert node["state"] == "active"


# ---------------------------------------------------------------------------
# learning_mutations.delete_node
# ---------------------------------------------------------------------------


class TestDeleteNode:
    def test_delete_removes_node_and_touching_edges(self, tmp_path):
        graph = make_graph()
        backup = tmp_path / "backup.json"
        result = delete_node(graph, "skill-a", backup)
        assert result["ok"] is True
        assert result["edges_removed"] == 2
        assert "deleted" in result["message"]
        assert [n["id"] for n in graph["nodes"]] == ["skill-b", "memory:MEM-X-001"]
        assert graph["edges"] == []
        # backup contém o grafo original, com o nó removido preservado
        saved = json.loads(backup.read_text(encoding="utf-8"))
        assert [n["id"] for n in saved["nodes"]] == ["skill-a", "skill-b", "memory:MEM-X-001"]

    def test_delete_keeps_untouched_edges(self, tmp_path):
        graph = make_graph()
        result = delete_node(graph, "skill-b", tmp_path / "b.json")
        assert result["ok"] is True
        assert result["edges_removed"] == 1
        assert graph["edges"] == [{"source": "memory:MEM-X-001", "target": "skill-a"}]
        assert [n["id"] for n in graph["nodes"]] == ["skill-a", "memory:MEM-X-001"]

    def test_delete_node_without_edges_key(self, tmp_path):
        graph = {"nodes": [{"id": "x"}, {"id": "y"}]}
        result = delete_node(graph, "x", tmp_path / "b.json")
        assert result["ok"] is True
        assert result["edges_removed"] == 0
        assert [n["id"] for n in graph["nodes"]] == ["y"]
        assert graph["edges"] == []

    def test_delete_unknown_node_fails_without_backup(self, tmp_path):
        graph = make_graph()
        backup = tmp_path / "backup.json"
        result = delete_node(graph, "nope", backup)
        assert result["ok"] is False
        assert "not found" in result["message"]
        assert len(graph["nodes"]) == 3
        assert not backup.exists()

    def test_delete_backup_failure_keeps_node(self, tmp_path):
        graph = make_graph()
        blocker = tmp_path / "blocker.txt"
        blocker.write_text("i am a file", encoding="utf-8")
        result = delete_node(graph, "skill-a", blocker / "backup.json")
        assert result["ok"] is False
        assert "backup failed" in result["message"]
        assert len(graph["nodes"]) == 3
