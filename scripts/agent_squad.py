"""
O que é: plano de controle local, baseado em arquivos, para o Agents Squad.
Responsabilidade: validar contratos, governar work items, gates, handoffs e memória.
Pra que serve: expor uma API Python e uma CLI para operar o fluxo do squad.
Comportamento em falha: rejeita entradas inválidas com SquadError e retorna código 1 na CLI.
Conexões: usa contratos, templates, LocalAgentDB, integrações locais e o Hive-Mind opcional.

O módulo não executa operações de rede, deploy, git, CAB ou credenciais por conta
própria; integrações externas opcionais são chamadas sem shell e falham de modo seguro.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from contextlib import contextmanager
from functools import wraps
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import yaml
from jsonschema import Draft202012Validator

from governed_io import LockTimeoutError, atomic_write_text, file_lock
from local_agent_db import LocalAgentDB
from project_context import ProjectContextError, find_project_root, resolve_project_context


class SquadError(RuntimeError):
    pass


ID_RE = re.compile(r"^(EPIC|US|TASK|BUG|REL|EVOL|STUDY|SPIKE)-[A-Z0-9-]+$")
AGENT_RE = re.compile(r"^[a-z0-9-]+$")

WORK_ITEM_DIRS: list[str] = [
    "discovery", "stories", "specs", "plans", "adr", "design",
    "implementation", "tests", "security", "evaluation", "observability",
    "performance", "release", "reviews", "findings", "gate-decisions",
    "handoffs", "memory/agents", "memory/shared", "memory/deltas",
    "traceability", "documentation", "analysis", "operations", "platform",
    "source", "census", "dispositions", "mappings", "candidates"
]


def now() -> str:
    """Retorna o timestamp UTC atual no formato ISO-8601."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_yaml(path: Path) -> dict[str, Any]:
    """Lê e faz parse seguro de um arquivo YAML em formato de dicionário."""
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise SquadError(f"YAML inválido em {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise SquadError(f"Esperado objeto YAML em {path}")
    return value


def write_yaml(path: Path, value: dict[str, Any]) -> None:
    """Serializa e grava um dicionário YAML por substituição atômica."""
    atomic_write_text(
        path,
        yaml.safe_dump(value, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def contains_control(path: Path) -> bool:
    """Verifica se um arquivo contém caracteres de controle inválidos."""
    data = path.read_bytes()
    return any(byte < 32 and byte not in (9, 10, 13) for byte in data)


def locked_artifact_mutation(method: Any) -> Any:
    """Executa uma mutação pública sob o lock do work item resolvido."""
    @wraps(method)
    def wrapped(self: "AgentSquad", item: Path | str, *args: Any, **kwargs: Any) -> Any:
        item_path = self._item(item)
        with self._artifact_lock(item_path):
            return method(self, item_path, *args, **kwargs)

    return wrapped


class AgentSquad:
    """Control plane e motor de governança local do squad de agentes.

    Arquitetura multi-projeto:
      - ``root``: runtime do squad (agentes, skills, contratos, templates).
      - ``project_name``: nome do projeto consumidor. Quando fornecido, os work items
        são criados em ``<root>/work/<project_name>/<WORK-ID>``. Quando ``None``, usa
        a raiz ``<root>/work/<WORK-ID>`` (modo legado).
      - ``banco`` sempre fica em ``<root>/banco/``, separado de work/.
    """

    def __init__(self, root: Path, project_name: str | None = None):
        """Inicializa o squad carregando contratos, workflow e registros.

        Args:
            root: raiz do runtime do squad (agentes, skills, configs globais).
            project_name: nome do projeto consumidor. Default: ``None`` (modo legado).
        """
        self.root = Path(root).resolve()
        self.project_name = project_name
        self.contracts = self.root / "contracts"
        self.templates = self.root / "templates"
        self.registry = read_yaml(self.root / "config/agent-registry.yaml")
        self.workflow = read_yaml(self.root / "config/workflow.yaml")
        self.skills_catalog = read_yaml(self.root / "config/skills-catalog.yaml")
        self.agents = {entry["id"]: entry for entry in self.registry.get("agents", [])}
        self.agent_ids = {entry["id"] for entry in self.registry.get("agents", [])}
        self.catalog_entries = {
            entry["path"]: entry for entry in self.skills_catalog.get("catalog", [])
        }
        self.gate_ids = set(self.workflow.get("gates", {}))
        self.states = {entry["id"] for entry in self.workflow.get("states", [])}

    def _schema(self, name: str) -> dict[str, Any]:
        return json.loads((self.contracts / name).read_text(encoding="utf-8"))

    def _validate(self, value: dict[str, Any], schema: str) -> None:
        errors = sorted(Draft202012Validator(self._schema(schema)).iter_errors(value), key=lambda e: list(e.path))
        if errors:
            detail = "; ".join(f"{'.'.join(map(str, e.path)) or '<root>'}: {e.message}" for e in errors)
            raise SquadError(f"Contrato {schema} inválido: {detail}")

    def validate_foundation(self) -> list[str]:
        """Validate optional contracts without activating source content."""
        pairs = {
            "source-artifact.schema.json": "source-artifact.yaml",
            "disposition.schema.json": "disposition.yaml",
            "squad.schema.json": "squad.yaml",
            "route.schema.json": "route.yaml",
            "task-template.schema.json": "task-template.yaml",
            "workflow-template.schema.json": "workflow-template.yaml",
        }
        errors: list[str] = []
        for schema_name, template_name in pairs.items():
            schema_path = self.contracts / schema_name
            template_path = self.templates / template_name
            if not schema_path.exists() or not template_path.exists():
                errors.append(f"foundation ausente: {schema_name}/{template_name}")
                continue
            try:
                Draft202012Validator.check_schema(self._schema(schema_name))
                self._validate(read_yaml(template_path), schema_name)
            except (SquadError, json.JSONDecodeError) as exc:
                errors.append(str(exc))
        return errors

    def _work_base(self) -> Path:
        """Resolve o diretório base de work items do projeto atual."""
        if self.project_name:
            return self.root / "work" / self.project_name
        return self.root / "work"

    def _db_path(self) -> Path:
        """Resolve o caminho do banco SQLite central compartilhado."""
        db_dir = self.root / "banco"
        db_dir.mkdir(parents=True, exist_ok=True)
        return db_dir / "squad.db"

    @contextmanager
    def _artifact_lock(self, item: Path) -> Iterator[None]:
        """Serializa mutações governadas por projeto e work item entre processos."""
        lock_path = self.root / ".locks" / (self.project_name or "_legacy") / f"{item.name}.lock"
        try:
            with file_lock(lock_path):
                yield
        except LockTimeoutError as exc:
            raise SquadError(str(exc)) from exc

    def init_work_item(self, work_id: str, risk: str, base: Path | None = None) -> Path:
        """Inicializa um novo work item sob lock exclusivo entre processos."""
        parent = Path(base) if base else self._work_base()
        item = (parent / work_id).resolve()
        with self._artifact_lock(item):
            return self._init_work_item_unlocked(work_id, risk, parent)

    def _init_work_item_unlocked(self, work_id: str, risk: str, parent: Path) -> Path:
        """Cria a árvore governada; deve ser chamado com o lock do item obtido."""
        if not ID_RE.fullmatch(work_id):
            raise SquadError(f"ID inválido: {work_id}")
        if risk not in {"low", "medium", "high", "critical"}:
            raise SquadError(f"Risco inválido: {risk}")
        item = (parent / work_id).resolve()
        if parent.resolve() not in item.parents:
            raise SquadError("work item fora da raiz permitida")
        item.mkdir(parents=True, exist_ok=False)

        for directory in WORK_ITEM_DIRS:
            (item / directory).mkdir(parents=True, exist_ok=True)

        kind_map = {
            "EPIC": "epic",
            "US": "story",
            "TASK": "task",
            "BUG": "bug",
            "REL": "release",
            "EVOL": "evolution",
            "STUDY": "study",
            "SPIKE": "spike",
        }
        kind = kind_map[work_id.split("-", 1)[0]]
        status = {
            "id": work_id,
            "type": kind,
            "state": "intake",
            "risk": risk,
            "owner": "delivery-orchestrator",
            "active_agents": [],
            "current_gate": None,
            "next_action": "Classificar e iniciar discovery.",
            "artifacts": ["status.yaml", "epic.md", "documentation/delivery-ledger.md"],
            "updated_at": now(),
        }
        self._validate(status, "work-item.schema.json")
        write_yaml(item / "status.yaml", status)
        ledger = (self.templates / "delivery-ledger.md").read_text(encoding="utf-8")
        initial_files = {
            "epic.md": f"# {work_id}\n\n## Objetivo\n\nA preencher durante discovery.\n",
            "product-goal.md": "# Product Goal\n\nA preencher após a validação do problema.\n",
            "backlog.md": "# Backlog\n\nA preencher pelo Product Owner.\n",
            "discovery/brief.md": "# Discovery Brief\n\nFatos, hipóteses e perguntas em aberto.\n",
            "memory/shared/summary.md": f"# Memória compartilhada — {work_id}\n\nNenhum fato publicado ainda.\n",
            "documentation/delivery-ledger.md": ledger,
        }
        for name, content in initial_files.items():
            atomic_write_text(item / name, content, encoding="utf-8")
        return item

    def _item(self, value: Path | str) -> Path:
        """Resolve o caminho absoluto de um work item existente."""
        value_path = Path(value)
        if value_path.is_absolute():
            candidate = value_path.resolve()
        else:
            parts = value_path.parts
            if len(parts) >= 2 and parts[0] == "work":
                candidate = (self.root / value_path).resolve()
                if self.project_name and len(parts) >= 3 and parts[1] == self.project_name:
                    candidate = (self.root / "work" / self.project_name / Path(*parts[2:])).resolve()
            else:
                candidate = (self._work_base() / value_path).resolve()

        if not (candidate / "status.yaml").exists():
            raise SquadError(f"work item inválido: {value}")
        return candidate

    def _item_reference(self, item: Path, value: str, label: str) -> Path:
        """Resolve uma referência do work item sem permitir fuga ou arquivo ausente."""
        reference = Path(value)
        if reference.is_absolute():
            raise SquadError(f"{label} precisa ser caminho relativo: {value}")
        target = (item / reference).resolve()
        if item not in target.parents:
            raise SquadError(f"{label} fora do work item: {value}")
        if not target.exists():
            raise SquadError(f"{label} inexistente: {value}")
        return target

    @locked_artifact_mutation
    def create_handoff(
        self,
        item: Path | str,
        sender: str,
        recipient: str,
        summary: str,
        artifacts: list[str],
        evidence: list[str],
        memory_delta: str,
        next_gate: str | None = None,
    ) -> dict[str, Any]:
        """Cria e valida um handoff formal entre dois agentes com artefatos e evidências."""
        item_path = self._item(item)
        if sender not in self.agent_ids or recipient not in self.agent_ids:
            raise SquadError("from/to precisam existir no agent-registry")
        if not artifacts or not evidence:
            raise SquadError("handoff exige artefato e evidência")
        if next_gate is not None and next_gate not in self.gate_ids:
            raise SquadError(f"gate desconhecido: {next_gate}")
        for reference in artifacts:
            self._item_reference(item_path, reference, "artefato")
        for reference in evidence:
            self._item_reference(item_path, reference, "evidência")
        self._item_reference(item_path, memory_delta, "delta de memória")

        status = read_yaml(item_path / "status.yaml")
        seq = len(list((item_path / "handoffs").glob("HANDOFF-*.yaml"))) + 1
        value = {
            "id": f"HANDOFF-{status['id']}-{seq:03d}",
            "work_item_id": status["id"],
            "from": sender,
            "to": recipient,
            "created_at": now(),
            "status": "ready",
            "summary": summary,
            "artifacts": artifacts,
            "decisions": [],
            "open_questions": [],
            "risks": [],
            "evidence": evidence,
            "memory_delta": memory_delta,
            "next_gate": next_gate,
            "acceptance": {
                "criteria_checked": ["handoff-schema-valid"],
                "recipient_ack_required": True,
            },
            "acknowledgement": {
                "status": "pending",
                "acknowledged_by": recipient,
                "acknowledged_at": None,
            },
        }
        self._validate(value, "handoff.schema.json")
        write_yaml(item_path / "handoffs" / f"{value['id']}.yaml", value)
        return value

    @locked_artifact_mutation
    def ack_handoff(self, item: Path | str, handoff_id: str, recipient: str) -> dict[str, Any]:
        """Confirma o recebimento de um handoff pelo destinatário autorizado."""
        item_path = self._item(item)
        path = item_path / "handoffs" / f"{handoff_id}.yaml"
        value = read_yaml(path)
        if recipient != value["to"]:
            raise SquadError("somente o destinatário pode aceitar o handoff")
        value["acknowledgement"] = {
            "status": "accepted",
            "acknowledged_by": recipient,
            "acknowledged_at": now(),
        }
        self._validate(value, "handoff.schema.json")
        write_yaml(path, value)
        return value

    @locked_artifact_mutation
    def decide_gate(
        self,
        item: Path | str,
        gate_id: str,
        decider: str,
        criteria: list[tuple[str, str]],
        evidence: list[str],
        human_approved_by: str | None = None,
        human_evidence: str | None = None,
    ) -> dict[str, Any]:
        """Avalia e emite formalmente uma decisão de gate (G1 a G6)."""
        item_path = self._item(item)
        if gate_id not in self.gate_ids:
            raise SquadError(f"gate desconhecido: {gate_id}")
        if decider not in self.agent_ids or not criteria or not evidence:
            raise SquadError("decisor, critérios e evidências são obrigatórios")
        gate = self.workflow["gates"][gate_id]
        authorized = set(gate.get("owners", [gate.get("owner")])) - {None}
        if decider not in authorized:
            raise SquadError(f"decisor não autorizado para {gate_id}: {decider}")
        expected = set(gate.get("criteria", []))
        provided = [name for name, _ in criteria]
        if len(provided) != len(set(provided)):
            raise SquadError("critério de gate duplicado")
        if set(provided) != expected:
            missing = sorted(expected - set(provided))
            extra = sorted(set(provided) - expected)
            raise SquadError(f"critérios divergentes; ausentes={missing}; extras={extra}")
        from gate_validators import validate_gate

        executed = validate_gate(gate_id, item_path)
        executable_names = {
            "bdd-specification-valid",
            "tdd-cycle-valid",
            "clean-code-executed",
            "tests-executed",
            "security-executed",
            "acceptance-bdd-executed",
            "regression-executed",
            "coverage-executed",
        }
        required_executable = expected & executable_names
        executed_results = {
            finding["criterion"]: finding["status"].lower()
            for finding in executed.get("findings", [])
            if finding.get("criterion") in required_executable
        }
        if set(executed_results) != required_executable:
            missing = sorted(required_executable - set(executed_results))
            raise SquadError(f"validador executável não cobriu os critérios obrigatórios: {missing}")
        claimed_results = dict(criteria)
        divergences = sorted(
            name for name in required_executable
            if claimed_results[name] != executed_results[name]
        )
        if divergences:
            raise SquadError(f"resultados declarados divergem da verificação executável: {divergences}")
        criteria = sorted({**claimed_results, **executed_results}.items())
        for reference in evidence:
            self._item_reference(item_path, reference, "evidência de gate")

        status = read_yaml(item_path / "status.yaml")
        human_required = bool(gate.get("human_required"))
        condition = gate.get("human_required_when")
        if condition == "risk-medium-high-critical" and status["risk"] != "low":
            human_required = True
        if condition == "business-acceptance":
            human_required = True
        if human_required and (not human_approved_by or not human_evidence):
            raise SquadError(f"{gate_id} exige aprovação humana e evidência")
        if human_evidence:
            self._item_reference(item_path, human_evidence, "evidência de aprovação humana")

        human_approval = {
            "required": human_required,
            "status": "approved" if human_approved_by else "not_required",
            "approved_by": human_approved_by,
            "evidence": human_evidence,
        }
        results = [{"name": name, "result": result} for name, result in criteria]
        decision = "approved" if all(entry["result"] in {"pass", "not_applicable"} for entry in results) else "changes_requested"
        value = {
            "decision_id": f"GD-{status['id']}-{gate_id.upper()}",
            "gate_id": gate_id,
            "work_item_id": status["id"],
            "decision": decision,
            "decider": decider,
            "criteria": results,
            "evidence": evidence,
            "human_approval": human_approval,
            "conditions": [],
            "valid_until": None,
            "decided_at": now(),
        }
        self._validate(value, "gate-decision.schema.json")
        write_yaml(item_path / "gate-decisions" / f"{value['decision_id']}.yaml", value)
        return value

    @locked_artifact_mutation
    def record_memory(
        self,
        item: Path | str,
        author: str,
        statement: str,
        source: str,
        kind: str = "fact",
    ) -> dict[str, Any]:
        """Registra um delta de memória e atualiza o resumo compartilhado do work item."""
        item_path = self._item(item)
        if author not in self.agent_ids:
            raise SquadError("autor precisa existir no agent-registry")
        self._item_reference(item_path, source, "fonte da memória")
        status = read_yaml(item_path / "status.yaml")
        seq = len(list((item_path / "memory/deltas").glob("MEM-*.yaml"))) + 1
        value = {
            "id": f"MEM-{status['id']}-{seq:03d}",
            "work_item_id": status["id"],
            "author": author,
            "recorded_at": now(),
            "entries": [{
                "kind": kind,
                "statement": statement,
                "source": source,
                "confidence": "high",
                "sensitivity": "internal",
                "invalidates_when": None,
            }],
        }
        self._validate(value, "memory-delta.schema.json")
        write_yaml(item_path / "memory/deltas" / f"{value['id']}.yaml", value)
        shared = item_path / "memory/shared/summary.md"
        shared_content = shared.read_text(encoding="utf-8")
        shared_content += f"\n- [{value['id']}] {statement} (fonte: {source})\n"
        atomic_write_text(shared, shared_content, encoding="utf-8")
        return value

    def discover(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        """Pesquisa somente o catálogo aprovado; intake e quarentena nunca entram."""
        from scripts.skill_selector import DiscoveredSkill, select_skills_for_task

        candidates: list[DiscoveredSkill] = []
        for relative, entry in sorted(self.catalog_entries.items()):
            path = self.root / relative / "SKILL.md"
            description = entry.get("description", "")
            metadata: dict[str, Any] = {}
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
                if text.startswith("---"):
                    end = text.find("\n---", 3)
                    if end != -1:
                        data = yaml.safe_load(text[3:end]) or {}
                        if isinstance(data, dict):
                            description = data.get("description", description)
                            metadata = {k: v for k, v in data.items() if k not in ("name", "description")}
            except (OSError, yaml.YAMLError):
                pass
            candidates.append(DiscoveredSkill(
                name=path.parent.name,
                path=relative,
                source="catalog",
                description=description,
                metadata=metadata,
            ))

        ranked = select_skills_for_task(query, candidates, max_results=limit, min_score=0.1)
        return [
            {"path": r.skill.path, "score": r.score, "description": r.skill.description}
            for r in ranked
        ]

    def activation_packet(
        self,
        agent: str,
        *,
        assigned: list[str] | None = None,
        discovered: list[str] | None = None,
        item: Path | str | None = None,
    ) -> dict[str, Any]:
        """Resolve uma ativação local sem executar agente, rede ou ação externa."""
        if agent not in self.agents:
            raise SquadError(f"agente desconhecido: {agent}")
        assigned = assigned or []
        discovered = discovered or []
        entry = self.agents[agent]
        manifest = read_yaml(self.root / entry["manifest"])
        allowed = {skill["path"] for skill in manifest.get("assigned", [])}
        unknown = sorted(set(assigned) - allowed)
        if unknown:
            raise SquadError(f"skill não atribuída a {agent}: {', '.join(unknown)}")
        maximum = int(manifest.get("discovery", {}).get("maximum_loaded", 0))
        if len(discovered) > maximum:
            raise SquadError(f"discovery excede o limite de {maximum} skills")
        invalid_discovered = sorted(set(discovered) - set(self.catalog_entries))
        if invalid_discovered:
            raise SquadError(
                "skill descoberta não está no catálogo aprovado: "
                + ", ".join(invalid_discovered)
            )

        native = [skill["path"] for skill in manifest.get("native", [])]
        selected = native + assigned + discovered
        if len(selected) > 7:
            raise SquadError(
                f"limite de skills excedido para {agent}: "
                f"{len(native)} native + {len(assigned)} assigned + {len(discovered)} discovered = {len(selected)} > 7"
            )
        skill_files = []
        for relative in selected:
            candidate = self.root / relative
            if candidate.is_dir():
                skill_file = candidate / "SKILL.md"
                if not skill_file.exists():
                    raise SquadError(f"SKILL.md ausente: {relative}")
                skill_files.append(skill_file.relative_to(self.root).as_posix())
            elif candidate.is_file() and candidate.suffix == ".py" and relative.startswith("integrations/"):
                skill_files.append(relative)
            else:
                raise SquadError(f"skill inválida (nem diretório com SKILL.md nem engine Python): {relative}")

        packet: dict[str, Any] = {
            "agent": agent,
            "prompt": f"{entry['path']}/PROMPT.md",
            "native": native,
            "assigned": assigned,
            "discovered": discovered,
            "load_order": skill_files,
            "handoff_schema": manifest["handoff"]["schema"],
        }
        if item is not None:
            work_item = self._item(item)
            status = read_yaml(work_item / "status.yaml")
            packet["work_item"] = work_item.relative_to(self._work_base()).as_posix()
            memory_root = (
                f"work/{self.project_name}/{status['id']}"
                if self.project_name
                else f"work/{status['id']}"
            )
            packet["memory"] = {
                key: value.replace("work/<WORK-ID>", memory_root)
                for key, value in manifest["memory"].items()
            }
        return packet

    def validate_work_item(self, item: Path | str) -> list[str]:
        """Valida a conformidade de schemas, ausência de caracteres de controle e integridade do work item."""
        item_path = self._item(item)
        status = read_yaml(item_path / "status.yaml")
        self._validate(status, "work-item.schema.json")
        errors = []
        for path in item_path.rglob("*"):
            if path.is_file() and contains_control(path):
                errors.append(f"caractere de controle: {path.relative_to(item_path)}")
        for path in (item_path / "handoffs").glob("HANDOFF-*.yaml"):
            handoff = read_yaml(path)
            self._validate(handoff, "handoff.schema.json")
            if handoff["acknowledgement"]["status"] == "pending":
                errors.append(f"handoff sem ACK: {path.name}")
        for path in (item_path / "gate-decisions").glob("*.yaml"):
            self._validate(read_yaml(path), "gate-decision.schema.json")
        return errors

    def audit(self) -> list[str]:
        """Audita a integridade do catálogo de skills, manifestos e mapeamentos de agentes."""
        errors = []
        for path in (self.root / "agents").glob("**/*.md"):
            if contains_control(path):
                errors.append(f"controle em {path.relative_to(self.root)}")
        for template in self.templates.glob("*.yaml"):
            try:
                value = read_yaml(template)
                schema = {
                    "handoff.yaml": "handoff.schema.json",
                    "memory-delta.yaml": "memory-delta.schema.json",
                    "work-item-status.yaml": "work-item.schema.json",
                }.get(template.name)
                if schema:
                    self._validate(value, schema)
            except SquadError as exc:
                errors.append(str(exc))
        actual_paths = set()
        for scan_dir in ("skills", "integrations"):
            for path in (self.root / scan_dir).rglob("SKILL.md"):
                posix = path.as_posix()
                if "skills/discovery/intake/" in posix:
                    continue
                if "skills/discovery/quarantine/" in posix:
                    continue
                if "/vendor/" in posix or "/__pycache__/" in posix:
                    continue
                actual_paths.add(path.parent.relative_to(self.root).as_posix())
        for path in (self.root / "integrations").glob("*.py"):
            if path.name != "__init__.py":
                actual_paths.add(path.relative_to(self.root).as_posix())
        catalog_paths = set(self.catalog_entries)
        for path in sorted(actual_paths - catalog_paths):
            errors.append(f"skill ativa fora do catálogo: {path}")
        for path in sorted(catalog_paths - actual_paths):
            errors.append(f"skill catalogada ausente: {path}")
        assigned_by: dict[str, set[str]] = {}
        for agent, entry in self.agents.items():
            manifest = read_yaml(self.root / entry["manifest"])
            for skill in manifest.get("assigned", []):
                path = skill["path"]
                assigned_by.setdefault(path, set()).add(agent)
                if path not in catalog_paths:
                    errors.append(f"manifesto aponta skill não catalogada: {agent}: {path}")
            for skill in manifest.get("native", []):
                path = skill["path"]
                assigned_by.setdefault(path, set()).add(agent)
        for path in sorted(catalog_paths - set(assigned_by)):
            errors.append(f"skill ativa sem agente atribuído: {path}")
        for path, entry in self.catalog_entries.items():
            declared = set(entry.get("assigned_to", []))
            resolved = assigned_by.get(path, set())
            if declared != resolved:
                errors.append(f"assigned_to divergente: {path}")
        return errors

    @locked_artifact_mutation
    def compact_memory(self, item: Path | str) -> dict[str, Any]:
        """Compacta a memória compartilhada (summary.md) de um work item usando Anchored Iterative Summarization."""
        work_item = self._item(item)
        summary_path = work_item / "memory" / "shared" / "summary.md"
        if not summary_path.is_file():
            raise SquadError(f"summary.md ausente em {work_item}")

        content = summary_path.read_text(encoding="utf-8")
        lines = content.splitlines()
        original_lines = len(lines)

        deltas = []
        for p in (work_item / "memory" / "deltas").glob("MEM-*.yaml"):
            try:
                deltas.append(read_yaml(p))
            except SquadError:
                pass

        facts = [d["statement"] for d in deltas if d.get("kind") == "fact"]
        decisions = [d["statement"] for d in deltas if d.get("kind") == "decision"]
        risks = [d["statement"] for d in deltas if d.get("kind") == "risk"]

        compacted = [
            f"# Shared Memory Summary — {work_item.name}",
            f"> Compactado automaticamente em {now()} via Anchored Iterative Summarization.",
            "",
            "## Invariantes & Fatos Críticos",
        ]
        if facts:
            compacted.extend(f"- {f}" for f in facts[-10:])
        else:
            compacted.append("- Nenhum fato formalmente registrado.")

        compacted.extend(["", "## Decisões Tomadas"])
        if decisions:
            compacted.extend(f"- {d}" for d in decisions[-10:])
        else:
            compacted.append("- Nenhuma decisão consolidada.")

        compacted.extend(["", "## Riscos & Pendências Ativas"])
        if risks:
            compacted.extend(f"- {r}" for r in risks[-5:])
        else:
            compacted.append("- Sem riscos em aberto.")

        new_content = "\n".join(compacted) + "\n"
        atomic_write_text(summary_path, new_content, encoding="utf-8")

        return {
            "work_item": work_item.name,
            "original_lines": original_lines,
            "compacted_lines": len(compacted),
            "reduction_ratio": round(1.0 - (len(compacted) / max(1, original_lines)), 2),
        }

    @locked_artifact_mutation
    def track_tokens(
        self,
        item: Path | str,
        agent: str,
        step: str,
        prompt_tokens: int,
        completion_tokens: int,
        cost_usd: float = 0.0,
    ) -> dict[str, Any]:
        """Registra o consumo de tokens e atualiza o bloco de métricas do status.yaml."""
        work_item = self._item(item)
        status_path = work_item / "status.yaml"
        status = read_yaml(status_path)

        try:
            db = LocalAgentDB(
                db_path=self._db_path(),
                project_id=self.project_name,
                allow_legacy=self.project_name is None,
            )
            db.record_token_metrics(status["id"], agent, step, prompt_tokens, completion_tokens, cost_usd)
            summary = db.get_token_summary(status["id"])
        except (OSError, ValueError):
            summary = {
                "total_prompt_tokens": prompt_tokens,
                "total_completion_tokens": completion_tokens,
                "total_cost_usd": cost_usd,
                "total_steps": 1,
            }

        status.setdefault("metrics", {})
        status["metrics"]["total_prompt_tokens"] = summary["total_prompt_tokens"]
        status["metrics"]["total_completion_tokens"] = summary["total_completion_tokens"]
        status["metrics"]["total_cost_usd"] = summary["total_cost_usd"]
        status["metrics"]["total_steps"] = summary["total_steps"]
        status["updated_at"] = now()

        write_yaml(status_path, status)
        return summary

    def decide_quorum(
        self,
        item: Path | str,
        gate: str,
        threshold: float = 0.67,
    ) -> dict[str, Any]:
        """Avalia a decisão de um gate baseado no quórum de votação bizantina registrado no banco local."""
        work_item = self._item(item)
        status = read_yaml(work_item / "status.yaml")

        db = LocalAgentDB(
            db_path=self._db_path(),
            project_id=self.project_name,
            allow_legacy=self.project_name is None,
        )
        return db.evaluate_quorum(status["id"], gate, threshold=threshold)

    def run_integration_engine(self, engine: str, **kwargs: Any) -> dict[str, Any]:
        """Executa um motor de integração do diretório integrations/ com validação de path."""
        engines_dir = (self.root / "integrations").resolve()
        script = (engines_dir / f"{engine}.py").resolve()
        if engines_dir not in script.parents or not script.is_file():
            return {"status": "error", "error": f"motor não encontrado ou inválido: {engine}"}
        try:
            args = ["python", str(script)]
            for key, value in kwargs.items():
                args.extend([f"--{key.replace('_', '-')}", str(value)])
            result = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=120,
                cwd=str(self._work_base()),
            )
            if result.returncode == 0:
                try:
                    return json.loads(result.stdout)
                except json.JSONDecodeError:
                    return {"status": "ok", "output": result.stdout}
            return {"status": "error", "stderr": result.stderr, "stdout": result.stdout}
        except (OSError, subprocess.SubprocessError) as exc:
            return {"status": "unavailable", "error": str(exc)}


def _parse_criteria(criteria_args: list[str]) -> list[tuple[str, str]]:
    """Faz o parsing dos argumentos de critérios nome=resultado para avaliação de gate."""
    criteria = []
    for raw in criteria_args:
        if "=" not in raw:
            raise SquadError(f"critério inválido: {raw}")
        name, result = raw.split("=", 1)
        criteria.append((name.strip(), result.strip()))
    return criteria


def _build_parser() -> argparse.ArgumentParser:
    """Constrói o parser da CLI sem executar comandos."""
    parser = argparse.ArgumentParser(description="Control plane local do squad de agentes")
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Raiz do runtime do squad (agentes, skills, contratos)",
    )
    parser.add_argument(
        "--project-name",
        type=str,
        default=None,
        help="Identificador do projeto; sobrescreve o marcador local para compatibilidade",
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=None,
        help="Raiz do projeto consumidor que contém .agents_squad/config/project.yaml",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init-work-item")
    init.add_argument("--id", required=True)
    init.add_argument("--risk", default="medium", choices=["low", "medium", "high", "critical"])

    disc = sub.add_parser("discover")
    disc.add_argument("--query", required=True)
    disc.add_argument("--limit", type=int, default=10)

    hand = sub.add_parser("create-handoff")
    hand.add_argument("--work-item", required=True)
    hand.add_argument("--from", dest="sender", required=True)
    hand.add_argument("--to", dest="recipient", required=True)
    hand.add_argument("--summary", required=True)
    hand.add_argument("--artifacts", nargs="+", required=True)
    hand.add_argument("--evidence", nargs="+", required=True)
    hand.add_argument("--memory-delta", required=True)
    hand.add_argument("--next-gate")

    ack = sub.add_parser("ack-handoff")
    ack.add_argument("--work-item", required=True)
    ack.add_argument("--handoff", required=True)
    ack.add_argument("--agent", required=True)

    gate = sub.add_parser("decide-gate")
    gate.add_argument("--work-item", required=True)
    gate.add_argument("--gate", required=True)
    gate.add_argument("--decider", required=True)
    gate.add_argument("--criteria", nargs="+", required=True, help="nome=pass|fail|not_applicable")
    gate.add_argument("--evidence", nargs="+", required=True)
    gate.add_argument("--human-approved-by")
    gate.add_argument("--human-evidence")

    validate = sub.add_parser("validate-work-item")
    validate.add_argument("--work-item", required=True)

    mem = sub.add_parser("record-memory")
    mem.add_argument("--work-item", required=True)
    mem.add_argument("--author", required=True)
    mem.add_argument("--statement", required=True)
    mem.add_argument("--source", required=True)
    mem.add_argument("--kind", default="fact", choices=["fact", "decision", "dependency", "risk", "pending"])

    activate = sub.add_parser("activate-agent")
    activate.add_argument("--agent", required=True)
    activate.add_argument("--work-item")
    activate.add_argument("--assigned", nargs="*", default=[])
    activate.add_argument("--discovered", nargs="*", default=[])

    sub.add_parser("audit")
    sub.add_parser("validate-foundation")

    compact = sub.add_parser("compact-memory")
    compact.add_argument("--work-item", required=True)

    track = sub.add_parser("track-tokens")
    track.add_argument("--work-item", required=True)
    track.add_argument("--agent", required=True)
    track.add_argument("--step", required=True)
    track.add_argument("--prompt-tokens", type=int, required=True)
    track.add_argument("--completion-tokens", type=int, required=True)
    track.add_argument("--cost", type=float, default=0.0)

    quorum = sub.add_parser("decide-quorum")
    quorum.add_argument("--work-item", required=True)
    quorum.add_argument("--gate", required=True)
    quorum.add_argument("--threshold", type=float, default=0.67)

    engine = sub.add_parser("run-engine")
    engine.add_argument("--engine", required=True)
    engine.add_argument("--work-item", required=True)

    review = sub.add_parser("review-learning")
    review.add_argument("--kind", required=True, choices=["gate", "handoff"])
    review.add_argument("--artifact", required=True)
    review.add_argument("--dry-run", action="store_true")
    review.add_argument("--model", default="granite4.1:3b")

    curator = sub.add_parser("curator-cycle")
    curator.add_argument("--backup-dir", default=None)

    correct = sub.add_parser("auto-correct")
    correct.add_argument("--kind", required=True,
                         choices=["gate-rejected", "blocked", "incident"])
    correct.add_argument("--source", required=True)
    correct.add_argument("--apply", action="store_true")

    ins = sub.add_parser("insights")
    ins.add_argument("--project-id", dest="project_id", default=None)
    return parser


def _execute_command(squad: AgentSquad, args: argparse.Namespace) -> int:
    """Executa o comando já validado pelo parser e retorna seu código de saída."""
    if args.command == "init-work-item":
        print(squad.init_work_item(args.id, args.risk))
    elif args.command == "discover":
        print(json.dumps(squad.discover(args.query, args.limit), ensure_ascii=False, indent=2))
    elif args.command == "create-handoff":
        handoff = squad.create_handoff(
            args.work_item,
            args.sender,
            args.recipient,
            args.summary,
            args.artifacts,
            args.evidence,
            args.memory_delta,
            args.next_gate,
        )
        print(json.dumps(handoff, ensure_ascii=False, indent=2))
    elif args.command == "ack-handoff":
        print(json.dumps(squad.ack_handoff(args.work_item, args.handoff, args.agent), ensure_ascii=False, indent=2))
    elif args.command == "decide-gate":
        criteria = _parse_criteria(args.criteria)
        decision = squad.decide_gate(
            args.work_item,
            args.gate,
            args.decider,
            criteria,
            args.evidence,
            args.human_approved_by,
            args.human_evidence,
        )
        print(json.dumps(decision, ensure_ascii=False, indent=2))
    elif args.command == "validate-work-item":
        errors = squad.validate_work_item(args.work_item)
        if not errors:
            print("WORK_ITEM_OK")
            return 0
        print("\n".join(errors))
        return 1
    elif args.command == "record-memory":
        memory = squad.record_memory(args.work_item, args.author, args.statement, args.source, args.kind)
        print(json.dumps(memory, ensure_ascii=False, indent=2))
    elif args.command == "activate-agent":
        packet = squad.activation_packet(
            args.agent,
            assigned=args.assigned,
            discovered=args.discovered,
            item=args.work_item,
        )
        if args.work_item:
            packet["memory_bridge"] = (
                "sinapse_* tools from the 'sinapse-hivemind' MCP server registered in the provider"
            )
        print(json.dumps(packet, ensure_ascii=False, indent=2))
    elif args.command == "audit":
        errors = squad.audit()
        if not errors:
            print("AUDIT_OK")
            return 0
        print("\n".join(errors))
        return 1
    elif args.command == "validate-foundation":
        errors = squad.validate_foundation()
        if not errors:
            print("FOUNDATION_OK")
            return 0
        print("\n".join(errors))
        return 1
    elif args.command == "compact-memory":
        result = squad.compact_memory(args.work_item)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.command == "track-tokens":
        print(json.dumps(
            squad.track_tokens(
                args.work_item,
                args.agent,
                args.step,
                args.prompt_tokens,
                args.completion_tokens,
                args.cost,
            ),
            ensure_ascii=False,
            indent=2,
        ))
    elif args.command == "decide-quorum":
        print(json.dumps(squad.decide_quorum(args.work_item, args.gate, args.threshold), ensure_ascii=False, indent=2))
    elif args.command == "run-engine":
        print(json.dumps(squad.run_integration_engine(args.engine, work_item=args.work_item), ensure_ascii=False, indent=2))
    elif args.command == "review-learning":
        from background_review import main as review_learning_main

        argv = ["--kind", args.kind, "--artifact", args.artifact,
                "--root", str(squad.root), "--model", args.model]
        if args.dry_run:
            argv.append("--dry-run")
        return review_learning_main(argv)
    elif args.command == "curator-cycle":
        from curator_cycle import main as curator_cycle_main

        backup_dir = args.backup_dir or str(squad.root / "backups")
        return curator_cycle_main(["--root", str(squad.root), "--backup-dir", backup_dir])
    elif args.command == "auto-correct":
        from auto_correction_trigger import main as auto_correct_main

        argv = ["--kind", args.kind, "--source", args.source, "--root", str(squad.root)]
        if args.apply:
            argv.append("--apply")
        return auto_correct_main(argv)
    elif args.command == "insights":
        from squad_insights import main as insights_main

        argv = []
        if args.project_id:
            argv += ["--project", args.project_id]
        return insights_main(argv)
    return 0


def main(argv: list[str] | None = None) -> int:
    """Ponto de entrada CLI para comandos de controle e governança do squad."""
    args = _build_parser().parse_args(argv)
    root = args.root.resolve()
    project_name = args.project_name
    marker_root = args.project_root or find_project_root(Path.cwd())
    if marker_root is not None and args.project_name is None:
        try:
            context = resolve_project_context(Path.cwd(), args.project_root)
        except ProjectContextError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        root = context.runtime_root
        project_name = context.project_id
    squad = AgentSquad(root, project_name=project_name)
    try:
        return _execute_command(squad, args)
    except SquadError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
