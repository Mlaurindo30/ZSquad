"""Alimentador determinístico do grafo de aprendizado do squad no FalkorDB.

O que é:
    Módulo stdlib (+ PyYAML) que coleta nós e arestas do conhecimento do squad
    — skills do catálogo, agentes do registry, deltas de memória e work items —
    e os carrega no grafo FalkorDB via RESP (protocolo Redis), emitindo um
    comando GRAPH.QUERY com MERGE por elemento (carga idempotente).

Responsabilidade:
    - Ler config/skills-catalog.yaml (`catalog:`), config/agent-registry.yaml
      (`agents:`), work/**/memory/deltas/MEM-*.yaml e work/*/status.yaml.
    - Produzir nós (:Skill {id,name,domain,source}), (:Agent {id}),
      (:Memory {id,author,kind}) e (:WorkItem {id,type,state}).
    - Produzir arestas ASSIGNED (agente→skill), IN_DOMAIN (skill→skill do mesmo
      domínio), FROM (memória→work item) e AUTHORED (agente→memória).
    - Enviar cada MERGE pelo transporte RESP e reportar contagens.

Pra que serve:
    Para popular o graph key "squad" no contêiner FalkorDB agents_squad_graph
    (localhost:6380) de forma reproduzível e idempotente, habilitando consultas
    de evolução, curadoria e trajetória do squad.

Comportamento em falha:
    - Catálogo/registry ausentes ou vazios geram zero nós daquele tipo;
      work_root inexistente gera zero memórias e zero work items (feed continua).
    - Entradas sem identificador (skill sem nome, agente/memória sem id) e
      arquivos MEM-*.yaml não-dicionário são ignoradas.
    - Arestas só ligam endpoints conhecidos (agente registrado, work item com
      status.yaml) para não criar nós fantasmas via MERGE.
    - Erros de conexão/protocolo (OSError, RESPError) propagam de feed(); o CLI
      imprime GRAPH_FEED_ERROR e devolve exit code 1.

Conexões:
    - FalkorDB RESP em host:port (padrão localhost:6380), graph key "squad",
      via GRAPH.QUERY.
    - Lê os YAML governados do squad (catálogo, registry, MEM-*.yaml, status.yaml).

Dependências & Imports:
    - Python stdlib: argparse, socket, pathlib.
    - PyYAML (leitura dos YAML do squad). Nenhuma dependência de rede externa
      além do próprio socket RESP.
"""

from __future__ import annotations

import argparse
import socket
from pathlib import Path
from typing import Any

import yaml

_PROJECT_ROOT = Path(__file__).resolve().parents[1]


class RESPError(Exception):
    """Erro de protocolo RESP ou de conexão reportado pelo transporte."""


class RESPClient:
    """Cliente RESP mínimo sobre socket stdlib, sem dependências externas.

    O que é:
        Transporte RESP (Redis Serialization Protocol) escrito com socket puro:
        codifica comandos como arrays de bulk strings e decodifica respostas
        simples (+), de erro (-), inteiras (:), bulk ($) e arrays (*).

    Responsabilidade:
        Conectar (lazy) em host:port, enviar `execute(*args)` como um comando
        RESP e devolver a resposta decodificada como listas/strings/int/None.

    Pra que serve:
        Falar com o FalkorDB (que fala RESP) sem depender de redis-py, mantendo
        o feeder portátil e testável com sockets falsos injetáveis.

    Comportamento em falha:
        Respostas de erro (`-`) e tipos não suportados levantam RESPError;
        conexão encerrada no meio de uma resposta levanta RESPError; falhas de
        socket propagam OSError para o chamador.

    Conexões:
        TCP direto com host:port (padrão localhost:6380) via
        socket.create_connection com timeout configurável.

    Dependências & Imports:
        Apenas `socket` da stdlib. O socket pode ser injetado em tests via
        `sock=` para nunca tocar a rede.
    """

    def __init__(self, host: str = "localhost", port: int = 6380,
                 timeout: float = 5.0, sock: Any = None) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self._sock = sock

    def connect(self):
        """Conecta lazy e devolve o socket ativo (injetado ou criado agora)."""
        if self._sock is None:
            self._sock = socket.create_connection(
                (self.host, self.port), timeout=self.timeout)
        return self._sock

    def execute(self, *args) -> Any:
        """Envia um comando RESP e devolve a resposta decodificada."""
        sock = self.connect()
        sock.sendall(self._encode(args))
        return self._read_reply(sock)

    def close(self) -> None:
        """Fecha o socket ativo, se houver; idempotente."""
        if self._sock is not None:
            self._sock.close()
            self._sock = None

    def __enter__(self) -> "RESPClient":
        self.connect()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    @staticmethod
    def _encode(args) -> bytes:
        """Codifica a lista de argumentos como array RESP de bulk strings."""
        chunks = [b"*%d\r\n" % len(args)]
        for arg in args:
            if isinstance(arg, str):
                payload = arg.encode("utf-8")
            elif isinstance(arg, bytes):
                payload = arg
            else:
                payload = str(arg).encode("utf-8")
            chunks.append(b"$%d\r\n%s\r\n" % (len(payload), payload))
        return b"".join(chunks)

    def _read_line(self, sock) -> bytes:
        """Lê uma linha RESP terminada em \\r\\n (tolera \\n solto)."""
        line = bytearray()
        while True:
            byte = sock.recv(1)
            if not byte:
                raise RESPError("conexão encerrada antes do fim da linha RESP")
            if byte == b"\n":
                if line.endswith(b"\r"):
                    del line[-1]
                return bytes(line)
            line += byte

    def _read_exactly(self, sock, n: int) -> bytes:
        """Lê exatamente n bytes (bulk string), mesmo em pedaços parciais."""
        data = bytearray()
        while len(data) < n:
            chunk = sock.recv(n - len(data))
            if not chunk:
                raise RESPError("conexão encerrada no meio de um bulk string")
            data += chunk
        return bytes(data)

    def _read_reply(self, sock) -> Any:
        """Decodifica uma resposta RESP (+, -, :, $, *) recursivamente."""
        line = self._read_line(sock)
        kind = line[:1]
        payload = line[1:]
        if kind == b"+":
            return payload.decode("utf-8")
        if kind == b"-":
            raise RESPError(payload.decode("utf-8"))
        if kind == b":":
            return int(payload)
        if kind == b"$":
            length = int(payload)
            if length == -1:
                return None
            data = self._read_exactly(sock, length)
            self._read_line(sock)
            return data.decode("utf-8")
        if kind == b"*":
            count = int(payload)
            if count == -1:
                return None
            return [self._read_reply(sock) for _ in range(count)]
        raise RESPError("tipo de resposta RESP não suportado: %r" % line)


def _text(value: object) -> str:
    """Normaliza None para string vazia; demais valores viram str."""
    return "" if value is None else str(value)


def _escape(value: object) -> str:
    """Escapa um valor para literal string Cypher entre aspas simples."""
    text = _text(value)
    text = text.replace("\\", "\\\\").replace("'", "\\'")
    text = text.replace("\r", "\\r").replace("\n", "\\n")
    return "'%s'" % text


class SquadGraphFeeder:
    """Coleta o conhecimento do squad e o carrega no FalkorDB via MERGE.

    O que é:
        Feeder determinístico: lê catálogo de skills, registry de agentes,
        deltas de memória e work items, e emite um MERGE Cypher por nó/aresta
        no graph key configurado.

    Responsabilidade:
        - collect_nodes(): nós :Skill, :Agent, :Memory e :WorkItem em ordem
          estável (catálogo/registry em ordem de arquivo; memórias por caminho
          ordenado; work items por diretório ordenado).
        - collect_edges(): arestas ASSIGNED, IN_DOMAIN, FROM e AUTHORED, só
          entre endpoints conhecidos.
        - feed(): envia cada Cypher como GRAPH.QUERY pelo transporte e devolve
          {"nodes": n, "edges": m, "queries": [...]}.

    Pra que serve:
        Para carregar o grafo de aprendizado do squad no FalkorDB de forma
        idempotente (MERGE) e auditável (lista exata de queries emitidas).

    Comportamento em falha:
        Arquivos/fontes ausentes ou vazios produzem zero elementos daquele
        tipo; entradas inválidas (sem id, YAML não-dicionário) são puladas;
        erros de transporte propagam de feed() (o CLI os converte em
        GRAPH_FEED_ERROR + exit 1).

    Conexões:
        Usa o transporte injetado (protocolo .execute(*args)) ou cria um
        RESPClient(host, port) padrão. Lê YAML do catálogo, registry e da
        árvore work/.

    Dependências & Imports:
        stdlib (pathlib) + PyYAML; sem I/O de rede além do transporte.
    """

    def __init__(self, host: str = "localhost", port: int = 6380,
                 graph_key: str = "squad", transport: Any = None,
                 catalog_path: Any = None, registry_path: Any = None,
                 work_root: Any = None) -> None:
        self.host = host
        self.port = port
        self.graph_key = graph_key
        self.transport = (transport if transport is not None
                          else RESPClient(host=host, port=port))
        self.catalog_path = (Path(catalog_path) if catalog_path is not None
                             else _PROJECT_ROOT / "config" / "skills-catalog.yaml")
        self.registry_path = (Path(registry_path) if registry_path is not None
                              else _PROJECT_ROOT / "config" / "agent-registry.yaml")
        self.work_root = (Path(work_root) if work_root is not None
                          else _PROJECT_ROOT / "work")

    # -- fontes governadas ------------------------------------------------

    def _load_catalog(self) -> list:
        """Devolve a lista `catalog:` do skills-catalog.yaml ([] se vazio)."""
        if not self.catalog_path.is_file():
            return []
        doc = yaml.safe_load(self.catalog_path.read_text(encoding="utf-8"))
        return (doc or {}).get("catalog") or []

    def _load_registry(self) -> list:
        """Devolve a lista `agents:` do agent-registry.yaml ([] se vazio)."""
        if not self.registry_path.is_file():
            return []
        doc = yaml.safe_load(self.registry_path.read_text(encoding="utf-8"))
        return (doc or {}).get("agents") or []

    def _iter_memory_deltas(self):
        """Itera os MEM-*.yaml da árvore work/ em ordem de caminho."""
        if not self.work_root.is_dir():
            return
        for path in sorted(self.work_root.rglob("MEM-*.yaml")):
            doc = yaml.safe_load(path.read_text(encoding="utf-8"))
            if not isinstance(doc, dict):
                continue
            yield doc

    def _iter_work_items(self):
        """Itera (id, doc) dos diretórios com status.yaml em ordem de nome."""
        if not self.work_root.is_dir():
            return
        for child in sorted(self.work_root.iterdir()):
            if not child.is_dir():
                continue
            status = child / "status.yaml"
            if not status.is_file():
                continue
            doc = yaml.safe_load(status.read_text(encoding="utf-8")) or {}
            yield _text(doc.get("id")) or child.name, doc

    # -- coleta de nós ----------------------------------------------------

    def collect_nodes(self) -> list:
        """Nós na ordem: Skills, Agents, Memories, WorkItems."""
        nodes: list = []
        nodes.extend(self._skill_nodes())
        nodes.extend(self._agent_nodes())
        nodes.extend(self._memory_nodes())
        nodes.extend(self._work_item_nodes())
        return nodes

    def _skill_nodes(self) -> list:
        nodes = []
        for entry in self._load_catalog():
            name = _text(entry.get("name"))
            if not name:
                continue
            nodes.append({"label": "Skill", "id": name,
                          "props": {"name": name,
                                    "domain": _text(entry.get("domain")),
                                    "source": _text(entry.get("source"))}})
        return nodes

    def _agent_nodes(self) -> list:
        nodes = []
        for entry in self._load_registry():
            agent_id = _text(entry.get("id"))
            if not agent_id:
                continue
            nodes.append({"label": "Agent", "id": agent_id, "props": {}})
        return nodes

    def _memory_nodes(self) -> list:
        nodes = []
        for doc in self._iter_memory_deltas():
            mem_id = _text(doc.get("id"))
            if not mem_id:
                continue
            entries = doc.get("entries") or []
            kind = _text(entries[0].get("kind")) if entries else ""
            nodes.append({"label": "Memory", "id": mem_id,
                          "props": {"author": _text(doc.get("author")),
                                    "kind": kind}})
        return nodes

    def _work_item_nodes(self) -> list:
        nodes = []
        for item_id, doc in self._iter_work_items():
            nodes.append({"label": "WorkItem", "id": item_id,
                          "props": {"type": _text(doc.get("type")),
                                    "state": _text(doc.get("state"))}})
        return nodes

    # -- coleta de arestas ------------------------------------------------

    def collect_edges(self) -> list:
        """Arestas na ordem: ASSIGNED, IN_DOMAIN, FROM, AUTHORED."""
        catalog = self._load_catalog()
        known_agents = {_text(entry.get("id")) for entry in self._load_registry()
                        if _text(entry.get("id"))}
        edges: list = []
        edges.extend(self._assigned_edges(catalog, known_agents))
        edges.extend(self._in_domain_edges(catalog))
        edges.extend(self._from_edges())
        edges.extend(self._authored_edges(known_agents))
        return edges

    def _assigned_edges(self, catalog: list, known_agents: set) -> list:
        edges = []
        for entry in catalog:
            name = _text(entry.get("name"))
            if not name:
                continue
            for agent in entry.get("assigned_to") or []:
                agent_id = _text(agent)
                if agent_id in known_agents:
                    edges.append({"src": ("Agent", agent_id),
                                  "rel": "ASSIGNED",
                                  "dst": ("Skill", name)})
        return edges

    def _in_domain_edges(self, catalog: list) -> list:
        by_domain: dict = {}
        for entry in catalog:
            name = _text(entry.get("name"))
            if not name:
                continue
            domain = _text(entry.get("domain"))
            by_domain.setdefault(domain, []).append(name)
        edges = []
        for domain in by_domain:
            names = sorted(set(by_domain[domain]))
            for i in range(len(names)):
                for j in range(i + 1, len(names)):
                    edges.append({"src": ("Skill", names[i]),
                                  "rel": "IN_DOMAIN",
                                  "dst": ("Skill", names[j])})
        return edges

    def _from_edges(self) -> list:
        known_work = {item_id for item_id, _doc in self._iter_work_items()}
        edges = []
        for doc in self._iter_memory_deltas():
            mem_id = _text(doc.get("id"))
            work_item_id = _text(doc.get("work_item_id"))
            if mem_id and work_item_id and work_item_id in known_work:
                edges.append({"src": ("Memory", mem_id),
                              "rel": "FROM",
                              "dst": ("WorkItem", work_item_id)})
        return edges

    def _authored_edges(self, known_agents: set) -> list:
        edges = []
        for doc in self._iter_memory_deltas():
            mem_id = _text(doc.get("id"))
            author = _text(doc.get("author"))
            if mem_id and author and author in known_agents:
                edges.append({"src": ("Agent", author),
                              "rel": "AUTHORED",
                              "dst": ("Memory", mem_id)})
        return edges

    # -- carga ------------------------------------------------------------

    def feed(self) -> dict:
        """Emite um GRAPH.QUERY MERGE por nó/aresta; devolve contagens e queries."""
        nodes = self.collect_nodes()
        edges = self.collect_edges()
        queries = [self._node_cypher(node) for node in nodes]
        queries += [self._edge_cypher(edge) for edge in edges]
        for cypher in queries:
            self.transport.execute("GRAPH.QUERY", self.graph_key, cypher)
        return {"nodes": len(nodes), "edges": len(edges), "queries": queries}

    @staticmethod
    def _node_cypher(node: dict) -> str:
        ident = _escape(node["id"])
        cypher = "MERGE (n:%s {id: %s})" % (node["label"], ident)
        if node["props"]:
            sets = ", ".join("n.%s = %s" % (key, _escape(val))
                             for key, val in node["props"].items())
            cypher = "%s SET %s" % (cypher, sets)
        return cypher

    @staticmethod
    def _edge_cypher(edge: dict) -> str:
        src_label, src_id = edge["src"]
        dst_label, dst_id = edge["dst"]
        # MATCH nos endpoints + MERGE apenas na relação: MERGE com o padrão
        # completo criaria nós duplicados quando a relação ainda não existe
        # (comportamento documentado do Cypher), mesmo com os nós já presentes.
        return ("MATCH (a:%s {id: %s}), (b:%s {id: %s}) MERGE (a)-[:%s]->(b)"
                % (src_label, _escape(src_id),
                   dst_label, _escape(dst_id), edge["rel"]))


def main(argv: list | None = None, feeder: SquadGraphFeeder | None = None) -> int:
    """CLI do feeder: carrega o grafo e reporta GRAPH_FEED_OK/ERROR.

    O que é:
        Entry point de linha de comando com --catalog, --registry, --work-root,
        --host, --port e --graph.

    Responsabilidade:
        Montar o SquadGraphFeeder (ou usar o injetado, para testes), executar
        feed() e imprimir o resultado.

    Pra que serve:
        Execução determinística pelo orchestrator: saída
        `GRAPH_FEED_OK nodes=<n> edges=<m>` e exit 0; `GRAPH_FEED_ERROR ...`
        e exit 1 em falha de conexão/protocolo.

    Comportamento em falha:
        OSError/RESPError (inclui conexão recusada) imprimem GRAPH_FEED_ERROR e
        devolvem 1; demais exceções propagam.

    Conexões:
        SquadGraphFeeder → transporte RESP (RESPClient padrão) no host:port do
        FalkorDB.

    Dependências & Imports:
        argparse da stdlib; nenhum estado global.
    """
    parser = argparse.ArgumentParser(
        description="Carrega o grafo de aprendizado do squad no FalkorDB (RESP).")
    parser.add_argument("--catalog", default=None,
                        help="caminho do config/skills-catalog.yaml")
    parser.add_argument("--registry", default=None,
                        help="caminho do config/agent-registry.yaml")
    parser.add_argument("--work-root", dest="work_root", default=None,
                        help="raiz da árvore work/")
    parser.add_argument("--host", default="localhost", help="host do FalkorDB")
    parser.add_argument("--port", type=int, default=6380, help="porta do FalkorDB")
    parser.add_argument("--graph", default="squad", help="graph key no FalkorDB")
    args = parser.parse_args(argv)

    active = feeder
    if active is None:
        active = SquadGraphFeeder(
            host=args.host, port=args.port, graph_key=args.graph,
            catalog_path=args.catalog, registry_path=args.registry,
            work_root=args.work_root)
    try:
        result = active.feed()
    except (OSError, RESPError) as exc:
        print("GRAPH_FEED_ERROR: %s" % exc)
        return 1
    print("GRAPH_FEED_OK nodes=%d edges=%d" % (result["nodes"], result["edges"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
