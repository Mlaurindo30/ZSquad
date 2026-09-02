"""Testes de cobertura (statements + branches) do squad_graph_feeder.

Cobertura total do módulo scripts/squad_graph_feeder.py sem abrir qualquer
conexão de rede real: o transporte RESP é sempre injetado (fakes de socket
ou mock de transporte) e o CLI é exercitado com feeders injetados ou grafos
vazios (zero chamadas ao transporte).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import scripts.squad_graph_feeder as sgf  # noqa: E402


# ---------------------------------------------------------------------------
# Fakes de infraestrutura (nunca tocam a rede)
# ---------------------------------------------------------------------------


class FakeSocket:
    """Socket fake com buffer único: recv fatia o buffer, sendall acumula."""

    def __init__(self, payload: bytes = b""):
        self._buf = payload
        self._chunks: list[bytes] = []
        self.sent = b""
        self.closed = False

    def sendall(self, data: bytes) -> None:
        self.sent += data

    def recv(self, size: int) -> bytes:
        if not self._buf and self._chunks:
            self._buf = self._chunks.pop(0)
        if not self._buf:
            return b""
        chunk = self._buf[:size]
        self._buf = self._buf[size:]
        return chunk

    def close(self) -> None:
        self.closed = True


class ChunkedFakeSocket(FakeSocket):
    """Socket fake que recarrega o buffer a partir de pedaços pré-scriptados."""

    def __init__(self, chunks: list[bytes]):
        super().__init__(b"")
        self._chunks = list(chunks)


class FakeTransport:
    """Transporte injetável que registra os comandos GRAPH.QUERY recebidos."""

    def __init__(self, error: Exception | None = None):
        self.calls: list[tuple] = []
        self.error = error

    def execute(self, *args):
        self.calls.append(args)
        if self.error is not None:
            raise self.error
        return ["OK"]


def _write_yaml(path: Path, data) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def _build_main_tree(tmp_path: Path):
    """Árvore canônica: 3 skills, 2 agentes válidos, 2 memórias, 2 work items."""
    catalog = _write_yaml(tmp_path / "catalog.yaml", {"catalog": [
        {"name": "skill-a", "path": "skills/a", "domain": "ai", "source": "local",
         "assigned_to": ["agent-a"], "description": "Skill A"},
        {"name": "skill-b", "path": "skills/b", "domain": "ai", "source": "catalog"},
        {"name": "skill-c", "path": "skills/c", "domain": "ops", "source": "remote",
         "assigned_to": ["agent-a", "agent-b"]},
    ]})
    registry = _write_yaml(tmp_path / "registry.yaml", {"agents": [
        {"path": "agents/x", "manifest": "agents/x/skills/manifest.yaml"},
        {"id": "agent-a", "path": "agents/a", "manifest": "agents/a/skills/manifest.yaml"},
        {"id": "agent-b", "path": "agents/b", "manifest": "agents/b/skills/manifest.yaml"},
    ]})
    work = tmp_path / "work"
    _write_yaml(work / "TASK-ONE" / "status.yaml",
                {"id": "TASK-ONE", "type": "task", "state": "done", "owner": "agent-a"})
    _write_yaml(work / "TASK-ONE" / "memory" / "deltas" / "MEM-TASK-ONE-1.yaml", {
        "id": "MEM-TASK-ONE-1", "work_item_id": "TASK-ONE", "author": "agent-a",
        "recorded_at": "2026-08-21T00:00:00Z",
        "entries": [{"kind": "decision", "statement": "Adotar RESP puro.",
                     "source": "docs/resp.md", "confidence": "high"}],
    })
    _write_yaml(work / "EPIC-TWO" / "status.yaml", {"type": "epic", "state": "intake"})
    _write_yaml(work / "EPIC-TWO" / "memory" / "deltas" / "MEM-EPIC-TWO-1.yaml", {
        "id": "MEM-EPIC-TWO-1", "recorded_at": "2026-08-21T00:00:00Z",
    })
    (work / "docs").mkdir(parents=True)
    (work / "docs" / "notes.md").write_text("notas", encoding="utf-8")
    (work / "README.md").write_text("work root", encoding="utf-8")
    return catalog, registry, work


EXPECTED_QUERIES = [
    "MERGE (n:Skill {id: 'skill-a'}) SET n.name = 'skill-a', n.domain = 'ai', n.source = 'local'",
    "MERGE (n:Skill {id: 'skill-b'}) SET n.name = 'skill-b', n.domain = 'ai', n.source = 'catalog'",
    "MERGE (n:Skill {id: 'skill-c'}) SET n.name = 'skill-c', n.domain = 'ops', n.source = 'remote'",
    "MERGE (n:Agent {id: 'agent-a'})",
    "MERGE (n:Agent {id: 'agent-b'})",
    "MERGE (n:Memory {id: 'MEM-EPIC-TWO-1'}) SET n.author = '', n.kind = ''",
    "MERGE (n:Memory {id: 'MEM-TASK-ONE-1'}) SET n.author = 'agent-a', n.kind = 'decision'",
    "MERGE (n:WorkItem {id: 'EPIC-TWO'}) SET n.type = 'epic', n.state = 'intake'",
    "MERGE (n:WorkItem {id: 'TASK-ONE'}) SET n.type = 'task', n.state = 'done'",
    "MATCH (a:Agent {id: 'agent-a'}), (b:Skill {id: 'skill-a'}) MERGE (a)-[:ASSIGNED]->(b)",
    "MATCH (a:Agent {id: 'agent-a'}), (b:Skill {id: 'skill-c'}) MERGE (a)-[:ASSIGNED]->(b)",
    "MATCH (a:Agent {id: 'agent-b'}), (b:Skill {id: 'skill-c'}) MERGE (a)-[:ASSIGNED]->(b)",
    "MATCH (a:Skill {id: 'skill-a'}), (b:Skill {id: 'skill-b'}) MERGE (a)-[:IN_DOMAIN]->(b)",
    "MATCH (a:Memory {id: 'MEM-TASK-ONE-1'}), (b:WorkItem {id: 'TASK-ONE'}) MERGE (a)-[:FROM]->(b)",
    "MATCH (a:Agent {id: 'agent-a'}), (b:Memory {id: 'MEM-TASK-ONE-1'}) MERGE (a)-[:AUTHORED]->(b)",
]


# ---------------------------------------------------------------------------
# RESPClient (socket sempre fake; create_connection sempre mockado)
# ---------------------------------------------------------------------------


def test_resp_simple_string_reply():
    fake = FakeSocket(b"+OK\r\n")
    client = sgf.RESPClient(sock=fake)
    assert client.execute("PING") == "OK"
    assert fake.sent == b"*1\r\n$4\r\nPING\r\n"


def test_resp_error_reply_raises():
    client = sgf.RESPClient(sock=FakeSocket(b"-ERR unknown command\r\n"))
    with pytest.raises(sgf.RESPError, match="ERR unknown command"):
        client.execute("PING")


def test_resp_integer_reply():
    client = sgf.RESPClient(sock=FakeSocket(b":42\r\n"))
    assert client.execute("INCR", "k") == 42


def test_resp_bulk_reply_with_partial_chunks():
    client = sgf.RESPClient(sock=ChunkedFakeSocket([b"$5\r", b"\nhe", b"llo", b"\r\n"]))
    assert client.execute("GET", "k") == "hello"


def test_resp_null_bulk_reply():
    client = sgf.RESPClient(sock=FakeSocket(b"$-1\r\n"))
    assert client.execute("GET", "k") is None


def test_resp_empty_bulk_reply():
    client = sgf.RESPClient(sock=FakeSocket(b"$0\r\n\r\n"))
    assert client.execute("GET", "k") == ""


def test_resp_array_reply_mixed_types():
    client = sgf.RESPClient(sock=FakeSocket(b"*2\r\n$3\r\nfoo\r\n:7\r\n"))
    assert client.execute("GRAPH.QUERY", "g", "MATCH (n) RETURN n") == ["foo", 7]


def test_resp_empty_array_reply():
    client = sgf.RESPClient(sock=FakeSocket(b"*0\r\n"))
    assert client.execute("KEYS", "*") == []


def test_resp_null_array_reply():
    client = sgf.RESPClient(sock=FakeSocket(b"*-1\r\n"))
    assert client.execute("BLPOP", "k") is None


def test_resp_nested_array_reply():
    client = sgf.RESPClient(sock=FakeSocket(b"*1\r\n*1\r\n+OK\r\n"))
    assert client.execute("GRAPH.QUERY", "g", "CYPHER") == [["OK"]]


def test_resp_error_inside_array_propagates():
    client = sgf.RESPClient(sock=FakeSocket(b"*1\r\n-ERR falhou\r\n"))
    with pytest.raises(sgf.RESPError, match="falhou"):
        client.execute("GRAPH.QUERY", "g", "CYPHER")


def test_resp_unsupported_reply_type_raises():
    client = sgf.RESPClient(sock=FakeSocket(b",3\r\n"))
    with pytest.raises(sgf.RESPError, match="não suportado"):
        client.execute("GRAPH.QUERY", "g", "CYPHER")


def test_resp_line_eof_raises():
    client = sgf.RESPClient(sock=FakeSocket(b"+OK"))
    with pytest.raises(sgf.RESPError, match="fim da linha"):
        client.execute("PING")


def test_resp_bare_newline_line_is_tolerated():
    client = sgf.RESPClient(sock=FakeSocket(b"+OK\n"))
    assert client.execute("PING") == "OK"


def test_resp_bulk_eof_raises():
    client = sgf.RESPClient(sock=FakeSocket(b"$5\r\nab"))
    with pytest.raises(sgf.RESPError, match="bulk"):
        client.execute("GET", "k")


def test_resp_encode_argument_types():
    fake = FakeSocket(b"+OK\r\n")
    client = sgf.RESPClient(sock=fake)
    assert client.execute("GRAPH.QUERY", b"squad", 7) == "OK"
    assert fake.sent == b"*3\r\n$11\r\nGRAPH.QUERY\r\n$5\r\nsquad\r\n$1\r\n7\r\n"


def test_resp_execute_without_arguments():
    fake = FakeSocket(b"*0\r\n")
    client = sgf.RESPClient(sock=fake)
    assert client.execute() == []
    assert fake.sent == b"*0\r\n"


def test_resp_connect_uses_create_connection(monkeypatch):
    fake = FakeSocket(b"+PONG\r\n")
    seen = {}

    def fake_connect(address, timeout=None):
        seen["address"] = address
        seen["timeout"] = timeout
        return fake

    monkeypatch.setattr("socket.create_connection", fake_connect)
    client = sgf.RESPClient(host="localhost", port=6380, timeout=2.5)
    assert client.connect() is fake
    assert seen == {"address": ("localhost", 6380), "timeout": 2.5}
    assert client.execute("PING") == "PONG"


def test_resp_context_manager_closes_and_close_without_socket_is_noop():
    fake = FakeSocket(b"+OK\r\n")
    with sgf.RESPClient(sock=fake) as client:
        assert client.execute("PING") == "OK"
    assert fake.closed is True
    sgf.RESPClient().close()  # sem socket: no-op, nenhuma rede


# ---------------------------------------------------------------------------
# Helpers de texto/cypher
# ---------------------------------------------------------------------------


def test_text_normalizes_none_to_empty_string():
    assert sgf._text(None) == ""
    assert sgf._text("x") == "x"
    assert sgf._text(7) == "7"


def test_escape_quotes_literals_safely():
    assert sgf._escape("it's") == "'it\\'s'"
    assert sgf._escape("a\\b") == "'a\\\\b'"
    assert sgf._escape("a\nb\rc") == "'a\\nb\\rc'"
    assert sgf._escape(None) == "''"
    assert sgf._escape(5) == "'5'"


# ---------------------------------------------------------------------------
# SquadGraphFeeder: coleta e feed (grafo canônico)
# ---------------------------------------------------------------------------


def test_collect_nodes_and_edges_shapes(tmp_path):
    catalog, registry, work = _build_main_tree(tmp_path)
    feeder = sgf.SquadGraphFeeder(transport=FakeTransport(), catalog_path=str(catalog),
                                  registry_path=str(registry), work_root=str(work))
    nodes = feeder.collect_nodes()
    assert [(n["label"], n["id"]) for n in nodes] == [
        ("Skill", "skill-a"), ("Skill", "skill-b"), ("Skill", "skill-c"),
        ("Agent", "agent-a"), ("Agent", "agent-b"),
        ("Memory", "MEM-EPIC-TWO-1"), ("Memory", "MEM-TASK-ONE-1"),
        ("WorkItem", "EPIC-TWO"), ("WorkItem", "TASK-ONE"),
    ]
    assert nodes[0]["props"] == {"name": "skill-a", "domain": "ai", "source": "local"}
    assert nodes[1]["props"] == {"name": "skill-b", "domain": "ai", "source": "catalog"}
    assert nodes[3]["props"] == {}
    assert nodes[5]["props"] == {"author": "", "kind": ""}
    assert nodes[6]["props"] == {"author": "agent-a", "kind": "decision"}
    assert nodes[7]["props"] == {"type": "epic", "state": "intake"}
    assert nodes[8]["props"] == {"type": "task", "state": "done"}
    edges = feeder.collect_edges()
    assert [(e["src"], e["rel"], e["dst"]) for e in edges] == [
        (("Agent", "agent-a"), "ASSIGNED", ("Skill", "skill-a")),
        (("Agent", "agent-a"), "ASSIGNED", ("Skill", "skill-c")),
        (("Agent", "agent-b"), "ASSIGNED", ("Skill", "skill-c")),
        (("Skill", "skill-a"), "IN_DOMAIN", ("Skill", "skill-b")),
        (("Memory", "MEM-TASK-ONE-1"), "FROM", ("WorkItem", "TASK-ONE")),
        (("Agent", "agent-a"), "AUTHORED", ("Memory", "MEM-TASK-ONE-1")),
    ]


def test_feed_full_graph_issues_exact_cypher_sequence(tmp_path):
    catalog, registry, work = _build_main_tree(tmp_path)
    transport = FakeTransport()
    feeder = sgf.SquadGraphFeeder(transport=transport, catalog_path=str(catalog),
                                  registry_path=str(registry), work_root=str(work))
    result = feeder.feed()
    assert result["nodes"] == 9
    assert result["edges"] == 6
    assert result["queries"] == EXPECTED_QUERIES
    assert transport.calls == [("GRAPH.QUERY", "squad", q) for q in EXPECTED_QUERIES]


def test_feed_empty_graph_issues_no_queries(tmp_path):
    catalog = tmp_path / "catalog.yaml"
    catalog.write_text("", encoding="utf-8")
    work = tmp_path / "work"
    work.mkdir()
    (work / "README.md").write_text("vazio", encoding="utf-8")
    (work / "docs").mkdir()
    transport = FakeTransport()
    feeder = sgf.SquadGraphFeeder(transport=transport, catalog_path=str(catalog),
                                  registry_path=str(tmp_path / "missing-registry.yaml"),
                                  work_root=str(work))
    assert feeder.feed() == {"nodes": 0, "edges": 0, "queries": []}
    assert transport.calls == []


def test_degenerate_entries_are_skipped(tmp_path):
    catalog = _write_yaml(tmp_path / "catalog.yaml", {"catalog": [
        {"path": "skills/sem-nome", "domain": "ai"},
        {"name": "skill-x", "domain": "ops", "assigned_to": ["ghost"]},
    ]})
    registry = tmp_path / "registry.yaml"
    registry.write_text("", encoding="utf-8")
    work = tmp_path / "work"
    (work / "WI-A").mkdir(parents=True)
    (work / "WI-A" / "status.yaml").write_text("", encoding="utf-8")
    (work / "D2" / "memory" / "deltas").mkdir(parents=True)
    (work / "D2" / "memory" / "deltas" / "MEM-EMPTY.yaml").write_text("", encoding="utf-8")
    _write_yaml(work / "D3" / "memory" / "deltas" / "MEM-NOID.yaml",
                {"author": "agent-a", "work_item_id": "WI-A"})
    _write_yaml(work / "D4" / "memory" / "deltas" / "MEM-ORPHAN.yaml",
                {"id": "MEM-ORPHAN", "work_item_id": "NOPE", "author": "ghost",
                 "entries": [{"kind": "risk"}]})
    feeder = sgf.SquadGraphFeeder(transport=FakeTransport(), catalog_path=str(catalog),
                                  registry_path=str(registry), work_root=str(work))
    result = feeder.feed()
    assert result["nodes"] == 3
    assert result["edges"] == 0
    assert result["queries"] == [
        "MERGE (n:Skill {id: 'skill-x'}) SET n.name = 'skill-x', n.domain = 'ops', n.source = ''",
        "MERGE (n:Memory {id: 'MEM-ORPHAN'}) SET n.author = 'ghost', n.kind = 'risk'",
        "MERGE (n:WorkItem {id: 'WI-A'}) SET n.type = '', n.state = ''",
    ]


def test_missing_work_root_yields_no_memories_or_work_items(tmp_path):
    catalog = _write_yaml(tmp_path / "catalog.yaml",
                          {"catalog": [{"name": "skill-a", "domain": "ai", "source": "local"}]})
    registry = _write_yaml(tmp_path / "registry.yaml", {"agents": [{"id": "agent-a"}]})
    feeder = sgf.SquadGraphFeeder(catalog_path=str(catalog), registry_path=str(registry),
                                  work_root=str(tmp_path / "nope"))
    assert isinstance(feeder.transport, sgf.RESPClient)
    nodes = feeder.collect_nodes()
    assert [(n["label"], n["id"]) for n in nodes] == [
        ("Skill", "skill-a"), ("Agent", "agent-a")]
    assert feeder.collect_edges() == []


def test_constructor_defaults_use_real_transport_and_repo_paths():
    feeder = sgf.SquadGraphFeeder()
    assert isinstance(feeder.transport, sgf.RESPClient)
    root = Path(sgf.__file__).resolve().parents[1]
    assert feeder.catalog_path == root / "config" / "skills-catalog.yaml"
    assert feeder.registry_path == root / "config" / "agent-registry.yaml"
    assert feeder.work_root == root / "work"
    assert feeder.host == "localhost"
    assert feeder.port == 6380
    assert feeder.graph_key == "squad"


# ---------------------------------------------------------------------------
# CLI main
# ---------------------------------------------------------------------------


def test_main_success_with_injected_feeder(tmp_path, capsys):
    catalog, registry, work = _build_main_tree(tmp_path)
    transport = FakeTransport()
    feeder = sgf.SquadGraphFeeder(transport=transport, catalog_path=str(catalog),
                                  registry_path=str(registry), work_root=str(work))
    rc = sgf.main(["--catalog", str(catalog), "--registry", str(registry),
                   "--work-root", str(work), "--host", "localhost",
                   "--port", "6380", "--graph", "squad"], feeder=feeder)
    assert rc == 0
    assert capsys.readouterr().out.strip() == "GRAPH_FEED_OK nodes=9 edges=6"
    assert len(transport.calls) == 15


def test_main_connection_error_prints_error_and_returns_1(tmp_path, capsys):
    catalog, registry, work = _build_main_tree(tmp_path)
    feeder = sgf.SquadGraphFeeder(
        transport=FakeTransport(error=ConnectionRefusedError("recusado")),
        catalog_path=str(catalog), registry_path=str(registry), work_root=str(work))
    rc = sgf.main([], feeder=feeder)
    assert rc == 1
    assert capsys.readouterr().out.startswith("GRAPH_FEED_ERROR")


def test_main_resp_error_returns_1(tmp_path, capsys):
    catalog, registry, work = _build_main_tree(tmp_path)
    feeder = sgf.SquadGraphFeeder(
        transport=FakeTransport(error=sgf.RESPError("cypher inválido")),
        catalog_path=str(catalog), registry_path=str(registry), work_root=str(work))
    rc = sgf.main([], feeder=feeder)
    assert rc == 1
    assert capsys.readouterr().out.startswith("GRAPH_FEED_ERROR")


def test_main_builds_own_feeder_and_succeeds_on_empty_graph(tmp_path, capsys):
    catalog = tmp_path / "catalog-inexistente.yaml"
    registry = tmp_path / "registry.yaml"
    registry.write_text("", encoding="utf-8")
    work = tmp_path / "work"
    work.mkdir()
    rc = sgf.main(["--catalog", str(catalog), "--registry", str(registry),
                   "--work-root", str(work)])
    assert rc == 0
    assert capsys.readouterr().out.strip() == "GRAPH_FEED_OK nodes=0 edges=0"


def test_main_constructed_feeder_connection_error(monkeypatch, tmp_path, capsys):
    catalog, registry, work = _build_main_tree(tmp_path)
    calls: list[tuple] = []

    class ExplodingClient:
        def __init__(self, host="localhost", port=6380, timeout=5.0, sock=None):
            self.host = host
            self.port = port

        def execute(self, *args):
            calls.append(args)
            raise OSError("falkordb fora do ar")

    monkeypatch.setattr(sgf, "RESPClient", ExplodingClient)
    rc = sgf.main(["--catalog", str(catalog), "--registry", str(registry),
                   "--work-root", str(work), "--host", "db.local", "--port", "7000",
                   "--graph", "custom"], feeder=None)
    assert rc == 1
    assert capsys.readouterr().out.startswith("GRAPH_FEED_ERROR")
    assert calls and calls[0][:2] == ("GRAPH.QUERY", "custom")


def test_module_entrypoint_guard(monkeypatch, capsys, tmp_path):
    import runpy
    import socket

    catalog = tmp_path / "catalog.yaml"
    catalog.write_text(
        "catalog:\n  - path: skills/x/SKILL.md\n    name: skill-x\n    domain: ai\n",
        encoding="utf-8",
    )

    def offline_connect(*args, **kwargs):
        raise OSError("offline")

    monkeypatch.setattr(socket, "create_connection", offline_connect)
    monkeypatch.setattr(sys, "argv", [
        "squad_graph_feeder.py", "--catalog", str(catalog),
        "--registry", str(tmp_path / "registry.yaml"),
        "--work-root", str(tmp_path / "work"),
    ])
    with pytest.raises(SystemExit) as exc:
        runpy.run_path(str(Path(sgf.__file__)), run_name="__main__")
    assert exc.value.code == 1
    assert capsys.readouterr().out.startswith("GRAPH_FEED_ERROR")
