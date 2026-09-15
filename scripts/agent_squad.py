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
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from functools import wraps
import hashlib
import json
import logging
from pathlib import Path
import re
import signal
import subprocess
import sys
from typing import Any, Iterator
import uuid

# Garante que o pacote ``scripts`` (que abriga este módulo) esteja importável
# independentemente do modo de invocação (script, ``python -m``, test runner).
_THIS_DIR = Path(__file__).resolve().parent
_ROOT_DIR = _THIS_DIR.parent
for _candidate in (str(_THIS_DIR), str(_ROOT_DIR)):
    if _candidate not in sys.path:
        sys.path.insert(0, _candidate)

from governed_io import LockTimeoutError, atomic_write_text, file_lock  # noqa: E402
from jsonschema import Draft202012Validator  # noqa: E402
from local_agent_db import LocalAgentDB  # noqa: E402
from project_context import (  # noqa: E402
    PathContainmentGuard,
    ProjectContextError,
    SDD_ACTIVATION_REL,
    SDD_POLICY_REL,
    archive_sdd_activation,
    build_sdd_activation,
    find_project_root,
    load_project_context,
    resolve_project_context,
    resolve_sdd_policy,
    sdd_required,
    write_sdd_activation,
)
from sdd_dispatch import FileSDDDispatcher  # noqa: E402
import yaml  # noqa: E402

logger = logging.getLogger(__name__)


def _signal_handler(sig, frame):
    logger.info("Received SIGINT, cleaning up...")
    sys.exit(0)



class SquadError(RuntimeError):
    pass


ID_RE = re.compile(r"^(EPIC|FEAT|US|TASK|BUG|REL|EVOL|STUDY|SPIKE)-[A-Z0-9-]+$")
AGENT_RE = re.compile(r"^[a-z0-9-]+$")

WORK_ITEM_DIRS: list[str] = [
    "discovery", "stories", "specs", "plans", "adr", "design",
    "implementation", "tests", "security", "evaluation", "observability",
    "performance", "release", "reviews", "findings", "gate-decisions",
    "handoffs",
    "traceability", "documentation", "analysis", "operations", "platform",
    "source", "census", "dispositions", "mappings", "candidates"
]


def now() -> str:
    """Retorna o timestamp UTC atual no formato ISO-8601."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# Integração SDD (T5 — Spec Kit): carregamento do adapter e mapeamentos
# ---------------------------------------------------------------------------

_SDD_ADAPTER_CACHE: dict[str, Any] = {}


def _sdd_adapter_modules() -> tuple[Any, Any]:
    """Carrega o adapter SDD (integrations/spec-kit/adapter) por caminho absoluto.

    O diretório pai contém hífen e não é importável pelo nome; o pacote é
    carregado via importlib.util.spec_from_file_location com
    submodule_search_locations, sem alterar sys.path global (plano seção 3).
    Retorna (pacote_adapter, módulo_policy) com cache por processo.
    """
    if "modules" not in _SDD_ADAPTER_CACHE:
        import importlib.util
        import sys

        base = _ROOT_DIR / "integrations" / "spec-kit" / "adapter"
        init = base / "__init__.py"
        if not init.is_file():
            raise SquadError(f"adapter SDD indisponível: {init}")
        spec = importlib.util.spec_from_file_location(
            "squad_sdd_adapter", init, submodule_search_locations=[str(base)]
        )
        if spec is None or spec.loader is None:
            raise SquadError("adapter SDD não pôde ser carregado (spec inválido)")
        module = importlib.util.module_from_spec(spec)
        # Registro prévio em sys.modules é obrigatório para os imports
        # relativos do pacote (from .contracts import ...) funcionarem.
        sys.modules["squad_sdd_adapter"] = module
        try:
            spec.loader.exec_module(module)
            policy_module = importlib.import_module("squad_sdd_adapter.policy")
        except BaseException:
            sys.modules.pop("squad_sdd_adapter", None)
            raise
        _SDD_ADAPTER_CACHE["modules"] = (module, policy_module)
    return _SDD_ADAPTER_CACHE["modules"]


# Estado atual -> estágios SDD que devem estar autorizados para o avanço.
# Fallback canônico; config/cycles.yaml (sdd_stages) pode declarar por ciclo.
# Conclusão de blueprint exige G1+G2 (plano §5): o estágio 'tasking' cobre G1
# via cadeia de dependência do adapter; 'implementation' cobre G1+G2+G3.
SDD_STATE_STAGES: dict[str, tuple[str, ...]] = {
    "blueprint": ("tasking",),
    "discovery": ("planning",),
    "product-ready": ("planning", "tasking"),
    "design": ("tasking",),
    "design-ready": ("tasking",),
    "scaffolding": ("implementation",),
    "readiness": ("implementation",),
    "ready-for-build": ("implementation",),
    # Retomada/bugfix entram DIRETO em implementation: o preflight (G1+G2+G3)
    # é exigido ao despachar a partir deste estado (plano §5, §8 cenário 7).
    "implementation": ("implementation",),
}

# gate_id como gravado pelo decide-gate (aliases do workflow) -> gate SDD do adapter.
SDD_DECISION_GATE_ALIASES: dict[str, str] = {
    "g1-product": "G1-product",
    "g2-design": "G2-design",
    "g3-readiness": "G3-readiness",
    "g3_readiness": "G3-readiness",
    "gt-design-review": "G3-readiness",
    "gt_design_review": "G3-readiness",
}

# gate_id de configuração (workflow.yaml) -> gate SDD que vincula inputs.
SDD_CONFIG_GATES: dict[str, str] = {
    "G1-product": "G1-product",
    "G2-design": "G2-design",
    "GT-design-review": "G3-readiness",
    "G3-readiness": "G3-readiness",
}


# ---------------------------------------------------------------------------
# CORR-1 (auditoria pós-entrega P1#1): família CLI `sdd` — sete comandos
# Spec Kit. Fonte dos contratos: REQUIRED_CONTEXT/STAGE_PERSONA do adapter
# (integrations/spec-kit/adapter/rendering.py) e fluxo do plano §5
# (Constitution -> Specify -> Clarify -> G1 -> Plan -> G2 -> Tasks/analyze
# -> G3 -> Implement).
# ---------------------------------------------------------------------------

SDD_SPECKIT_COMMANDS: tuple[str, ...] = (
    "constitution", "specify", "clarify", "plan", "tasks", "analyze", "implement",
)

# Comando Spec Kit -> estágio do adapter usado em validate_package (estrutura).
# Os esqueletos criados por `sdd init` garantem os documentos base; a validação
# do estágio correspondente é sempre executada (fail-closed).
SDD_COMMAND_VALIDATE_STAGE: dict[str, str] = {
    "constitution": "planning",
    "specify": "planning",
    "clarify": "planning",
    "plan": "planning",
    "tasks": "tasking",
    "analyze": "tasking",
    "implement": "implementation",
}

# Comando Spec Kit -> estágio do adapter usado em authorize (gates).
# constitution/specify/clarify PRECEDEM o G1 (plano §5): não há gate a
# autorizar ainda — authorize contra 'planning' exigiria G1 antes de existir.
# O fail-closed desses comandos permanece via validate_package; os comandos
# pós-G1 autorizam o estágio cujo STAGE_GATES corresponde aos gates exigidos.
SDD_COMMAND_AUTHORIZE_STAGE: dict[str, str | None] = {
    "constitution": None,
    "specify": None,
    "clarify": None,
    "plan": "planning",
    "tasks": "tasking",
    "analyze": "tasking",
    "implement": "implementation",
}

# Próximo gate exigido após o comando (fluxo do plano §5 + state_to_gate).
SDD_COMMAND_NEXT_GATE: dict[str, str | None] = {
    "constitution": None,
    "specify": None,
    "clarify": "G1-product",
    "plan": "G2-design",
    "tasks": "G3-readiness",
    "analyze": "G3-readiness",
    "implement": "G4-code-security",
}

# Input SDD -> arquivo gerado pelo esqueleto de `sdd init`.
SDD_PACKAGE_INPUT_FILES: dict[str, str] = {
    "spec": "spec.md",
    "clarifications": "clarifications.yaml",
    "plan": "plan.md",
    "tasks": "tasks.yaml",
}

# CORR-3: mapeamento de comando Spec Kit -> persona responsável.
# Alinhado com integrations/spec-kit/adapter/rendering.py (STAGE_PERSONA).
SDD_COMMAND_PERSONA: dict[str, str] = {
    "constitution": "requirements-analyst",
    "specify": "requirements-analyst",
    "clarify": "requirements-analyst",
    "plan": "solution-architect",
    "tasks": "delivery-orchestrator",
    "analyze": "delivery-orchestrator",
    "implement": "software-engineer",
}

# CORR-2 (passo 8 da auditoria): estado do work item -> comando Spec Kit cujo
# briefing governado se aplica AO estado atual no packet de activate-agent.
# Fonte: mesmo fluxo do plano §5 usado por SDD_STATE_STAGES e
# SDD_COMMAND_NEXT_GATE — blueprint conclui planejamento (G1+G2 => 'plan');
# scaffolding conclui tasking (G3 => 'tasks'); implementation e estados
# posteriores executam 'implement'. Estados sem estágio correspondente (ex.:
# done) não recebem briefing.
SDD_STATE_SPECKIT_STAGE: dict[str, str] = {
    "blueprint": "plan",
    "discovery": "specify",
    "product-ready": "clarify",
    "design": "plan",
    "design-ready": "plan",
    "scaffolding": "tasks",
    "readiness": "tasks",
    "ready-for-build": "tasks",
    "implementation": "implement",
    "code-security-review": "implement",
    "quality-validation": "implement",
    "governance-release": "implement",
}

# CORR-2 (P2/passos 4 da auditoria): regra canônica de exclusão de vendor no
# scanner de skills do audit. Código vendor GOVERNADO sob estes prefixos não é
# skill do Squad — sua integridade é atestada por
# integrations/spec-kit/upstream/UPSTREAM_FILES.sha256 (verify_snapshot.py),
# não pelo catálogo de skills. A exclusão é SEMPRE reportada como linha
# informativa na saída do audit (nunca silenciosa).
VENDOR_SKILL_SCAN_EXCLUSIONS: tuple[str, ...] = ("integrations/spec-kit/",)


def _sdd_error_message(errors: list[dict], action: str) -> str:
    """Formata erros SDD ({code, path, message}) em mensagem de SquadError."""
    detail = "; ".join(f"{e.get('code')}[{e.get('path')}]: {e.get('message')}" for e in errors)
    return f"Blocked by SDD ({action}): {detail}"


def _sdd_sha256_bytes(data: bytes) -> str:
    """Hash sha256 hexadecimal do conteúdo em bytes (esqueleto `sdd init`)."""
    return hashlib.sha256(data).hexdigest()


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


BINARY_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf",
    ".pyc", ".pyo", ".so", ".dll", ".exe", ".zip", ".gz", ".tar",
    ".db", ".sqlite", ".sqlite3", ".coverage", ".bin", ".woff", ".woff2",
}


def contains_control(path: Path) -> bool:
    """Verifica se um arquivo de texto contém caracteres de controle inválidos.

    Evidências binárias (imagens, .pyc, .coverage etc.) sempre têm bytes < 32
    e não são varredura de conteúdo textual malicioso; são ignoradas aqui.
    """
    # pathlib não trata ".coverage" como uma extensão (é um dotfile sem stem);
    # comparar pelo nome completo cobre esses casos além da extensão comum.
    if path.name.lower() in {".coverage"} or path.suffix.lower() in BINARY_EXTENSIONS or "__pycache__" in path.parts:
        return False
    data = path.read_bytes()
    # 9=tab, 10=LF, 13=CR, 27=ESC (sequências ANSI benignas em logs de build/terminal)
    return any(byte < 32 and byte not in (9, 10, 13, 27) for byte in data)


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

    def __init__(
        self,
        root: Path,
        project_name: str | None = None,
        allow_legacy: bool = False,
        project_root: Path | None = None,
        sdd_dispatcher: Any | None = None,
    ):
        """Inicializa o squad carregando contratos, workflow e registros.

        Args:
            root: raiz do runtime do squad (agentes, skills, contratos, templates).
            project_name: nome do projeto consumidor. Default: ``None`` (modo legado).
            allow_legacy: permite criar work items soltos em ``work/`` sem projeto.
                Use apenas em testes/utilitários; o fluxo governado exige projeto.
            project_root: raiz do projeto consumidor (contém ``.agents_squad``),
                usada para resolver a política SDD (T5). Quando ``None``, tenta
                resolver pelo marcador do projeto; sem marcador, modo legado.
        """
        self.root = Path(root).resolve()
        self.project_name = project_name
        self.allow_legacy = allow_legacy
        self.project_root = Path(project_root).resolve() if project_root is not None else None
        self.sdd_dispatcher = sdd_dispatcher or FileSDDDispatcher()
        self.contracts = self.root / "contracts"
        self.templates = self.root / "templates"
        self.registry = read_yaml(self.root / "config/agent-registry.yaml")
        self.workflow = read_yaml(self.root / "config/workflow.yaml")
        self.cycles = read_yaml(self.root / "config/cycles.yaml")
        self.skills_catalog = read_yaml(self.root / "config/skills-catalog.yaml")
        registry_agents = self.registry.get("agents", [])
        ordered_agents = sorted(registry_agents, key=lambda entry: entry.get("dispatchable") is False)
        self.agents = {entry["id"]: entry for entry in ordered_agents}
        self.agent_ids = {entry["id"] for entry in self.registry.get("agents", [])}
        primary_hosts = [
            entry for entry in self.registry.get("agents", [])
            if entry.get("provider_primary") is True
        ]
        if len(primary_hosts) != 1 or primary_hosts[0].get("dispatchable") is not False:
            raise SquadError("registry exige exatamente um provider_primary não despachável")
        self.dispatchable_agent_ids = {
            entry["id"] for entry in self.registry.get("agents", [])
            if entry.get("dispatchable", True)
        }
        self.catalog_entries = {
            entry["path"]: entry for entry in self.skills_catalog.get("catalog", [])
        }
        self.gate_ids = set(self.workflow.get("gates", {}))
        self.gate_aliases = self.workflow.get("gate_aliases", {})
        final_gates = {"G4-code-security", "G5-quality", "G6-governance-release"}
        if not final_gates <= self.gate_ids or final_gates & set(self.gate_aliases):
            raise SquadError("workflow exige gates finais nativos G4, G5 e G6")
        self.states = {entry["id"] for entry in self.workflow.get("states", [])}

    def get_gate(self, gate_id: str) -> dict[str, Any]:
        """Retorna as configurações do gate resolvendo aliases (ex: G1-product -> GT-entry)."""
        key = self.gate_aliases.get(gate_id, gate_id)
        if key not in self.gate_ids:
            raise SquadError(f"gate desconhecido: {gate_id}")
        return self.workflow["gates"][key]

    def validate_sod_snapshot(self, snapshot: dict[str, Any]) -> None:
        """Rejeita sobreposição de funções incompatíveis no risco governado."""
        risk = snapshot.get("risk")
        if risk not in {"medium", "high", "critical"}:
            return
        roles = {
            role: snapshot.get(role)
            for role in ("author", "reviewer", "approver", "executor")
            if snapshot.get(role) is not None
        }
        conflicts = self.workflow.get("segregation_of_duties", {}).get("conflicts", [])
        for first, second in conflicts:
            if roles.get(first) is not None and roles.get(first) == roles.get(second):
                raise SquadError(f"SoD conflict: {first} and {second}")
        if risk in {"high", "critical"} and len(set(roles.values())) != len(roles):
            raise SquadError("SoD conflict: high/critical roles must be pairwise distinct")

    def migrate_handoffs(self, item: Path | str, *, dry_run: bool = False) -> dict[str, Any]:
        """Migra handoffs v1 locais para v2 com backup e execução idempotente."""
        item_path = self._item(item)
        risk = read_yaml(item_path / "status.yaml")["risk"]
        migrated: list[str] = []
        skipped: list[str] = []
        for path in sorted((item_path / "handoffs").glob("HANDOFF-*.yaml")):
            value = read_yaml(path)
            if value.get("schema_version") == 2:
                skipped.append(path.name)
                continue
            reviewer = value.get("to") if value.get("to") != value.get("from") else None
            upgraded = dict(value)
            upgraded["schema_version"] = 2
            upgraded["sod_snapshot"] = {
                "risk": risk,
                "author": value["from"],
                "reviewer": reviewer,
                "approver": None,
                "executor": None,
                "independence_checked": reviewer is not None,
            }
            self._validate(upgraded, "handoff.schema.json")
            if not dry_run:
                backup = path.with_suffix(path.suffix + ".v1.bak")
                if not backup.exists():
                    atomic_write_text(backup, path.read_text(encoding="utf-8"), encoding="utf-8")
                write_yaml(path, upgraded)
            migrated.append(path.name)
        return {"dry_run": dry_run, "migrated": migrated, "skipped": skipped}

    def reclassify_work_item(
        self,
        work_id: str,
        *,
        from_type: str,
        to_type: str,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        """Reclassifica um item existente por transição governada e auditável."""
        transition = (from_type.lower(), to_type.lower())
        if transition not in {("evolution", "epic")}:
            raise SquadError(
                f"work item type transition not allowed: '{transition[0]}' -> '{transition[1]}'"
            )

        item = self._item(work_id)
        if self.project_name:
            PathContainmentGuard.validate_work_path(item, self.root, self.project_name)
        if item.name != work_id or Path(work_id).name != work_id:
            raise SquadError(f"work item target must be an exact ID: {work_id}")

        with self._artifact_lock(item):
            status_path = item / "status.yaml"
            before_bytes = status_path.read_bytes()
            status = read_yaml(status_path)
            current_type = str(status.get("type", "")).lower()
            if current_type == transition[1]:
                return {
                    "status": "already_reclassified",
                    "work_item": work_id,
                    "from_type": transition[0],
                    "to_type": transition[1],
                    "dry_run": dry_run,
                }
            if current_type != transition[0]:
                raise SquadError(
                    f"work item '{work_id}' type is '{current_type}', expected '{transition[0]}'"
                )
            if status.get("parent_id"):
                raise SquadError(f"target type 'epic' must not have parent_id: {status['parent_id']}")

            incompatible_children: list[str] = []
            for sibling in sorted(item.parent.iterdir()):
                sibling_status_path = sibling / "status.yaml"
                if sibling == item or not sibling.is_dir() or not sibling_status_path.is_file():
                    continue
                child_status = read_yaml(sibling_status_path)
                if child_status.get("parent_id") == work_id and str(child_status.get("type", "")).lower() != "feature":
                    incompatible_children.append(
                        f"{child_status.get('id', sibling.name)}:{child_status.get('type', 'EMPTY')}"
                    )
            if incompatible_children:
                raise SquadError(
                    "incompatible child work items for target type 'epic': " + ", ".join(incompatible_children)
                )

            updated = dict(status)
            updated["type"] = transition[1]
            updated["hierarchy_level"] = 1
            updated["updated_at"] = now()
            self._validate(updated, "work-item.schema.json")
            result: dict[str, Any] = {
                "status": "would_reclassify" if dry_run else "reclassified",
                "work_item": work_id,
                "from_type": transition[0],
                "to_type": transition[1],
                "dry_run": dry_run,
                "status_sha256_before": hashlib.sha256(before_bytes).hexdigest(),
                "preserved_directory": str(item),
                "compatible_children": [],
            }
            if dry_run:
                return result

            write_yaml(status_path, updated)
            after_bytes = status_path.read_bytes()
            audit = {
                **result,
                "executed_at": now(),
                "status_sha256_after": hashlib.sha256(after_bytes).hexdigest(),
                "rollback": "Restore the pre-change status.yaml from version control or the recorded before digest under independent review.",
            }
            evidence_dir = item / "evidence"
            evidence_dir.mkdir(parents=True, exist_ok=True)
            audit_path = evidence_dir / f"reclassification-{transition[0]}-to-{transition[1]}.json"
            atomic_write_text(audit_path, json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            result["audit_path"] = str(audit_path)
            result["status_sha256_after"] = audit["status_sha256_after"]
            return result

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
            "devops-config.schema.json": "devops.yaml",
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
        """Resolve o diretório base de work items do projeto atual com guarda de contenção."""
        base = (self.root / "work" / self.project_name).resolve() if self.project_name else (self.root / "work").resolve()
        if self.project_name:
            PathContainmentGuard.validate_work_path(base, self.root, self.project_name)
        return base

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

    def init_work_item(
        self,
        work_id: str,
        risk: str,
        base: Path | None = None,
        story_points: int | None = None,
        devops: bool = False,
        *,
        item_type: str | None = None,
        parent_id: str | None = None,
        force: bool = False,
    ) -> Path:
        """Inicializa um novo work item Full sob lock exclusivo entre processos."""
        if story_points is not None:
            if story_points > 8:
                raise SquadError("Story Points > 8 são estritamente bloqueados; divisão vertical (slicing) obrigatória.")
            if story_points not in {1, 2, 3, 5, 8}:
                raise SquadError("story_points deve usar Fibonacci 1, 2, 3, 5 ou 8")

        if base is None and not self.project_name and not self.allow_legacy:
            raise SquadError(
                "work item solto recusado: informe project_name (ou --project-name/--project-root) "
                "para criar em work/<projeto>/<WORK-ID>"
            )
        parent = Path(base) if base else self._work_base()
        
        is_explicit_type = item_type is not None
        resolved_type = (item_type if item_type is not None else self._legacy_type_for_id(work_id)).lower()
        
        # 4-Tier Hierarchy Parent Validation
        hierarchy_levels = {"epic": 1, "feature": 2, "story": 3, "pbi": 3, "task": 4, "bug": 3}
        level = hierarchy_levels.get(resolved_type, 3)
        
        if resolved_type == "epic" and parent_id:
            raise SquadError("Epics cannot have a parent_id")

        if is_explicit_type:
            if resolved_type in {"feature"} and not parent_id:
                raise SquadError(f"Work item type '{resolved_type}' requires a parent_id of type 'epic'")
            elif resolved_type in {"story", "pbi"} and not parent_id:
                raise SquadError(f"Work item type '{resolved_type}' requires a parent_id of type 'feature'")
            elif resolved_type in {"task"} and not parent_id:
                raise SquadError(f"Work item type '{resolved_type}' requires a parent_id of type 'story'")

        if parent_id:
            parent_item_path = (parent / parent_id).resolve()
            if not (parent_item_path / "status.yaml").exists():
                raise SquadError(f"Parent work item '{parent_id}' does not exist at {parent_item_path}")
            parent_status = read_yaml(parent_item_path / "status.yaml")
            parent_type = str(parent_status.get("type", "")).lower()
            
            expected_parent_type = {
                "feature": "epic",
                "story": "feature",
                "pbi": "feature",
                "task": "story",
            }.get(resolved_type)
            
            if expected_parent_type and parent_type != expected_parent_type:
                raise SquadError(
                    f"Parent work item '{parent_id}' type '{parent_type}' does not match required parent type '{expected_parent_type}'"
                )

        # Query Before Create (QBC) protocol for Epics and Features
        if resolved_type in {"epic", "feature"} and not force:
            for candidate_path in parent.glob("*"):
                if candidate_path.is_dir() and (candidate_path / "status.yaml").exists():
                    try:
                        cand_status = read_yaml(candidate_path / "status.yaml")
                        cand_type = str(cand_status.get("type", "")).lower()
                        if cand_type == resolved_type:
                            cand_id = candidate_path.name
                            if work_id.lower() == cand_id.lower() or work_id.lower().split('-')[0] == cand_id.lower().split('-')[0]:
                                raise SquadError(
                                    f"QBC Violation: Duplicate {resolved_type} detected ({cand_id}). Use --force to override."
                                )
                    except Exception as exc:
                        if isinstance(exc, SquadError) and "QBC Violation" in str(exc):
                            raise
                        pass

        self._resolve_cycle_entry(resolved_type)
        item = (parent / work_id).resolve()
        
        if self.project_name:
            PathContainmentGuard.validate_work_path(item, self.root, self.project_name)

        with self._artifact_lock(item):
            item = self._init_work_item_unlocked(
                work_id, risk, parent, item_type=resolved_type, parent_id=parent_id, hierarchy_level=level
            )
            status_path = item / "status.yaml"
            status = read_yaml(status_path)
            needs_write = False
            if story_points is not None:
                status["story_points"] = story_points
                needs_write = True
            if parent_id is not None:
                status["parent_id"] = parent_id
                needs_write = True
            if level is not None:
                status["hierarchy_level"] = level
                needs_write = True

            # Verifica flag explícita ou devops.yaml ativo
            create_in_ado = devops
            if not create_in_ado:
                try:
                    from pathlib import Path as _Path
                    import sys as _sys
                    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent / "integrations"))
                    from devops_platform_connector import load_devops_config
                    devops_cfg = load_devops_config(parent)
                    create_in_ado = bool(devops_cfg.get("enabled", False) or devops_cfg.get("devops_enabled", False))
                except Exception:
                    pass

            if create_in_ado:
                try:
                    from pathlib import Path as _Path
                    import sys as _sys
                    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent / "integrations"))
                    from devops_platform_connector import DevOpsPlatformConnector, load_devops_config
                    connector = DevOpsPlatformConnector(root_path=_Path(__file__).resolve().parents[1])
                    config = load_devops_config(parent)

                    # Determinar tipo ADO baseado no risk/type
                    item_type = status.get("type", "story")
                    type_map = config.get("work_item_type_map", {})
                    wit_type = type_map.get(item_type, "User Story")

                    # Criar via conector
                    ado_item = None
                    try:
                        ado_item = connector.create_work_item(
                            title=work_id,
                            wit_type=wit_type,
                            area_path=config.get("area_path", "Arthemis\\agent-squad"),
                        )
                    except TypeError:
                        ado_item = connector.create_work_item(
                            item_type=wit_type,
                            title=work_id,
                            story_points=story_points,
                        )

                    ado_id = None
                    if ado_item:
                        if hasattr(ado_item, "id"):
                            ado_id = str(ado_item.id)
                        elif isinstance(ado_item, dict):
                            ado_id = str(ado_item.get("id"))
                        elif isinstance(ado_item, (int, str)):
                            ado_id = str(ado_item)

                    if ado_id:
                        status["devops_id"] = ado_id
                        needs_write = True
                except Exception as _e:
                    print(f"WARN init_work_item_devops_create_failed: {_e}")
                    logger.warning(f"Falha ao criar work item no Azure DevOps: {_e}")

            if needs_write:
                write_yaml(status_path, status)

            return item

    def _ledger_path(self, item: Path | str) -> Path:
        """Resolve o ledger exclusivamente dentro do work item governado."""
        item_path = Path(item).resolve()
        configured = self.workflow.get("work_item", {}).get("ledger")
        if not isinstance(configured, str):
            raise SquadError("ledger inválido no workflow")
        return self._item_reference(item_path, configured, "ledger")

    def init_light_item(self, work_id: str, risk: str, objective: str = "") -> Path:
        """Cria um flat artifact para tarefa Light (zero pastas, zero handoff, zero gate)."""
        if not self.project_name:
            raise SquadError("light-start exige project_name (use --project-name)")
        if not ID_RE.fullmatch(work_id):
            raise SquadError(f"ID inválido: {work_id}")
        if risk not in {"low", "medium"}:
            raise SquadError("Light aceita apenas risco low ou medium")
        light_dir = self._work_base() / "light"
        light_dir.mkdir(parents=True, exist_ok=True)
        artifact = light_dir / f"{work_id}.md"
        if artifact.exists():
            raise SquadError(f"light artifact já existe: {artifact}")
        content = (
            "---\n"
            f"id: {work_id}\n"
            "mode: light\n"
            f"risk: {risk}\n"
            f"created_at: {now()}\n"
            "owner: delivery-orchestrator\n"
            "touch: []\n"
            "---\n\n"
            "## Objetivo\n"
            f"{objective or '(preencher)'}\n\n"
            "## TDD\n"
            "- RED: (pendente)\n"
            "- GREEN: (pendente)\n\n"
            "## Evidência\n"
            "(pendente)\n"
        )
        artifact.write_text(content, encoding="utf-8")
        return artifact

    def init_project(
        self,
        project_name: str,
        project_root: Path,
        *,
        devops: bool = False,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Cria .agents_squad/config/project.yaml no projeto alvo e, opcionalmente,
        executa o ciclo de setup Azure DevOps (create → import → configure).

        Args:
            project_name: identificador do projeto (deve casar ``AGENT_RE``).
            project_root: raiz do projeto consumidor.
            devops: se True, chama azure_devops_lifecycle.run() após o vínculo.
            dry_run: se True, apenas retorna o plano sem alterar arquivos.

        Returns:
            dict com chaves: ``linked`` (bool), ``marker`` (Path|None),
            ``devops`` (bool), ``dry_run`` (bool).
        """
        if not AGENT_RE.fullmatch(project_name):
            raise SquadError(f"project_name inválido para AGENT_RE: {project_name}")
        project_root = Path(project_root).resolve()
        marker_dir = project_root / ".agents_squad" / "config"
        marker_path = marker_dir / "project.yaml"

        plan: dict[str, Any] = {
            "linked": False,
            "marker": None,
            "devops": devops,
            "dry_run": dry_run,
            "project_name": project_name,
            "project_root": str(project_root),
        }

        if dry_run:
            plan["marker"] = str(marker_path)
            return plan

        if marker_path.exists():
            raise SquadError(f"marcador já existe: {marker_path}")

        marker_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 2,
            "runtime": str(self.root),
            "project_id": project_name,
            "project_name": project_name,
            "project_root": str(project_root),
            "work_dir": str(self.root / "work" / project_name),
            "db_path": str(self.root / "banco" / "squad.db"),
            "overrides": {},
        }
        from governed_io import atomic_write_yaml
        atomic_write_yaml(marker_path, payload)

        if devops:
            from azure_devops_lifecycle import run as azure_devops_run
            azure_devops_run(project_name=project_name, project_root=project_root, dry_run=dry_run)

        plan["linked"] = True
        plan["marker"] = str(marker_path)
        return plan

    def check_timebox(self, item: Path | str) -> dict[str, Any]:
        """Verifica se a fase atual excedeu o timebox definido no workflow."""
        item_path = self._item(item)
        status = read_yaml(item_path / "status.yaml")
        phase = status.get("state", "")
        timeboxes = self.workflow.get("flow", {}).get("phase_timeboxes", {})
        limit = timeboxes.get(phase)
        if not limit:
            return {"phase": phase, "limit": None, "elapsed_minutes": None, "exceeded": False}
        started = status.get("phase_started_at")
        if not started:
            return {"phase": phase, "limit": limit, "elapsed_minutes": None, "exceeded": False}
        start = datetime.fromisoformat(started.replace("Z", "+00:00"))
        elapsed = (datetime.now(timezone.utc) - start).total_seconds() / 60
        return {"phase": phase, "limit": limit, "elapsed_minutes": round(elapsed, 1), "exceeded": elapsed > limit}

    def _init_work_item_unlocked(
        self, work_id: str, risk: str, parent: Path, *, item_type: str | None = None, parent_id: str | None = None, hierarchy_level: int = 3
    ) -> Path:
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

        kind = item_type if item_type is not None else self._legacy_type_for_id(work_id)
        cycle_name, cycle_def = self._resolve_cycle_entry(kind)
        entry_state = str(cycle_def["entry"])
        status = {
            "id": work_id,
            "type": kind,
            "cycle": cycle_name,
            "state": entry_state,
            "risk": risk,
            "owner": "delivery-orchestrator",
            "active_agents": [],
            "current_gate": None,
            "next_action": (
                "Iniciar implementação conforme ciclo."
                if entry_state == "implementation"
                else "Preencher blueprint.md e validar GT-entry."
            ),
            "artifacts": ["status.yaml", "blueprint.md", "epic.md", "documentation/delivery-ledger.md"],
            "updated_at": now(),
            "phase_started_at": now(),
            "hierarchy_level": hierarchy_level,
        }
        if parent_id:
            status["parent_id"] = parent_id

        self._validate(status, "work-item.schema.json")
        write_yaml(item / "status.yaml", status)
        ledger = (self.templates / "delivery-ledger.md").read_text(encoding="utf-8")
        blueprint = (self.templates / "blueprint.md").read_text(encoding="utf-8")
        initial_files = {
            "blueprint.md": blueprint.replace("<WORK-ID>", work_id),
            "epic.md": f"# {work_id}\n\n## Objetivo\n\nA preencher durante discovery.\n",
            "product-goal.md": "# Product Goal\n\nA preencher após a validação do problema.\n",
            "backlog.md": "# Backlog\n\nA preencher pelo Product Owner.\n",
            "discovery/brief.md": "# Discovery Brief\n\nFatos, hipóteses e perguntas em aberto.\n",
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

    # ------------------------------------------------------------------
    # SDD (T5): política por projeto, autorização de estágios e contexto
    # ------------------------------------------------------------------

    def _sdd_project_root(self) -> Path | None:
        """Resolve a raiz do projeto consumidor para a política SDD."""
        if self.project_root is not None:
            return self.project_root
        if not self.project_name:
            return None
        marker_root = find_project_root(self.root)
        if marker_root is None:
            return None
        try:
            context = load_project_context(marker_root)
        except ProjectContextError:
            return None
        if context.project_id != self.project_name:
            return None
        return context.project_root

    def _sdd_policy_state(self) -> str:
        """Estado da política SDD do projeto: 'active' ou 'legacy'."""
        project_root = self._sdd_project_root()
        if project_root is None:
            return "legacy"
        return resolve_sdd_policy(project_root).state

    def _sdd_active(self, item_path: Path) -> bool:
        """True quando o SDD é OBRIGATÓRIO e imposto para o work item (CORR-2/P1#5).

        Semântica ÚNICA, alinhada com ``adapter.authorize``/``is_sdd_required``:
        exige pacote SDD (``sdd/package.json``), política em estado ativo E
        ``sdd.required: true``. Política presente com ``sdd.required: false`` é
        NÃO obrigatória em todo o pipeline (decide-gate, advance, dispatch):
        tratada como compatível com legado — sem requisitos SDD impostos.
        Política ativa INVÁLIDA (ou ausente com registro de ativação durável,
        CORR-2/P1#4) continua ativa e obrigatória: o enforcement roda e falha
        fechado (``SDD_POLICY_INVALID``) — nunca desativa SDD por omissão.
        Sem pacote, o REQUISITO é verificado por :meth:`_sdd_missing_package_error`
        (MAJOR-2: fail-closed no advance/dispatch quando ``sdd.required: true``).
        """
        if not (item_path / "sdd" / "package.json").is_file():
            return False
        project_root = self._sdd_project_root()
        if project_root is None:
            return False
        if resolve_sdd_policy(project_root).state != "active":
            return False
        return sdd_required(project_root)

    def _sdd_missing_package_error(self, item_path: Path) -> str | None:
        """MAJOR-2: política ativa + pacote SDD ausente => bloqueia (fail-closed).

        Escape para itens criados ANTES da ativação exigiria uma data de
        ativação na política; ``contracts/sdd-policy.schema.json`` não possui
        esse campo (e é fechado: ``additionalProperties: false``), então o
        bloqueio é incondicional enquanto o campo não existir — decisão do
        revisor ("itens criados após a ativação exigem pacote, ou campo de
        política para fail-closed"). Projeto legado (sem política) não bloqueia.
        """
        if (item_path / "sdd" / "package.json").is_file():
            return None
        project_root = self._sdd_project_root()
        if project_root is None:
            return None
        resolution = resolve_sdd_policy(project_root)
        if resolution.state != "active":
            return None
        if not sdd_required(project_root):
            return None
        return (
            "SDD_MISSING_INPUT: work item sem pacote SDD (sdd/package.json) em projeto "
            "com sdd.required: true — itens sob política ativa exigem pacote SDD "
            "(plano §6/T5). Escape para itens criados antes da ativação não está "
            "implementado: o contrato de política não define data de ativação."
        )

    def _schema_work_item_types(self) -> set[str]:
        """Lê os tipos aceitos no schema para evitar uma segunda enumeração."""
        types = self._schema("work-item.schema.json").get("properties", {}).get("type", {}).get("enum")
        if not isinstance(types, list) or not all(isinstance(value, str) for value in types):
            raise SquadError("schema de work item não define enum de tipos válido")
        return set(types)

    @staticmethod
    def _legacy_type_for_id(work_id: str) -> str:
        """Mantém a derivação histórica usada por callers sem ``--type``."""
        kind_map = {
            "EPIC": "epic", "US": "story", "TASK": "task", "BUG": "bug",
            "REL": "release", "EVOL": "evolution", "STUDY": "study", "SPIKE": "spike",
        }
        prefix = work_id.split("-", 1)[0]
        try:
            return kind_map[prefix]
        except KeyError as exc:
            raise SquadError(f"ID inválido: {work_id}") from exc

    def _resolve_cycle_entry(self, item_type: str) -> tuple[str, dict[str, Any]]:
        """Resolve tipo -> ciclo -> entry usando somente ``config/cycles.yaml``."""
        valid_types = self._schema_work_item_types()
        if item_type not in valid_types:
            raise SquadError(f"tipo inválido: {item_type}")
        mapping = self.cycles.get("type_to_cycle")
        if not isinstance(mapping, dict) or set(mapping) != valid_types:
            raise SquadError("drift de configuração: type_to_cycle deve cobrir exatamente o enum do schema")
        cycle_name = mapping.get(item_type)
        cycles = self.cycles.get("cycles")
        if not isinstance(cycle_name, str) or not isinstance(cycles, dict) or cycle_name not in cycles:
            raise SquadError(f"ciclo inválido para tipo '{item_type}': {cycle_name}")
        cycle_def = cycles[cycle_name]
        if not isinstance(cycle_def, dict):
            raise SquadError(f"definição inválida para ciclo '{cycle_name}'")
        states = cycle_def.get("states")
        entry = cycle_def.get("entry")
        if not isinstance(states, list) or not states or not isinstance(entry, str) or entry not in states:
            raise SquadError(f"entry inválido para ciclo '{cycle_name}'")
        return cycle_name, cycle_def

    def _cycle_name_for_status(self, status: dict[str, Any]) -> str:
        """Identifica o ciclo persistido; itens legados derivam pelo tipo."""
        if "cycle" in status:
            cycle_name = status.get("cycle")
            if not isinstance(cycle_name, str) or not cycle_name:
                raise SquadError("status declara cycle inválido")
            return cycle_name
        item_type = status.get("type")
        if not isinstance(item_type, str):
            raise SquadError("status sem tipo válido para derivar ciclo")
        return self._resolve_cycle_entry(item_type)[0]

    def _sdd_stages_for_state(self, cycle_def: dict[str, Any], current_state: str) -> tuple[str, ...]:
        """Estágios SDD exigidos para sair do estado atual no ciclo dado (MINOR-4).

        Ordem de precedência:
        1. ``sdd_stages`` declarado em config/cycles.yaml para o estado;
        2. ``sdd_preflight: true`` no ciclo: o estado de ENTRADA do ciclo
           (ex.: bugfix entra direto em implementation) exige o preflight
           completo (G1+G2+G3 = estágio ``implementation``);
        3. fallback canônico SDD_STATE_STAGES (código).
        """
        declared = cycle_def.get("sdd_stages")
        if isinstance(declared, dict) and current_state in declared:
            return (str(declared[current_state]),)
        if cycle_def.get("sdd_preflight") and current_state == cycle_def.get("entry"):
            return ("implementation",)
        return SDD_STATE_STAGES.get(current_state, ())

    @staticmethod
    def _sdd_policy_valid_until(policy: dict[str, Any]) -> str | None:
        """TTL de decisão a partir da política (MINOR-3).

        Chave opcional ``valid_until_days`` (int >= 1, dias UTC a partir de
        agora). Sob o contrato atual ``contracts/sdd-policy.schema.json``
        (``additionalProperties: false``) a chave é REJEITADA por
        ``validate_policy`` — o decide-gate falha fechado antes de chegar
        aqui — portanto o TTL só se torna expressável quando o contrato for
        estendido no épico; enquanto isso ``valid_until`` permanece ``None``.
        """
        ttl = policy.get("valid_until_days")
        if isinstance(ttl, bool) or not isinstance(ttl, int) or ttl < 1:
            return None
        return (
            (datetime.now(timezone.utc) + timedelta(days=ttl))
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )

    def _sdd_load_decisions(self, item_path: Path) -> list[dict[str, Any]]:
        """Lê os GD-*.yaml do item normalizando gate_id para o gate SDD canônico."""
        decisions: list[dict[str, Any]] = []
        decisions_dir = item_path / "gate-decisions"
        if not decisions_dir.is_dir():
            return decisions
        for dec_file in sorted(decisions_dir.glob("*.yaml")):
            try:
                record = read_yaml(dec_file)
            except SquadError:
                continue
            if not isinstance(record, dict):
                continue
            stored = str(record.get("gate_id", "")).lower()
            record["gate_id"] = SDD_DECISION_GATE_ALIASES.get(stored, record.get("gate_id"))
            decisions.append(record)
        return decisions

    @staticmethod
    def _sdd_valid_until_errors(decisions: list[dict[str, Any]], required_gates: set[str]) -> list[dict[str, Any]]:
        """Avalia valid_until com o relógio da camada CLI (o adapter não consulta relógio)."""
        errors: list[dict[str, Any]] = []
        now_dt = datetime.now(timezone.utc)
        for gate in sorted(required_gates):
            candidates = []
            for index, record in enumerate(decisions):
                if record.get("gate_id") != gate:
                    continue
                decided_at = record.get("decided_at")
                if not isinstance(decided_at, str) or not decided_at:
                    continue
                try:
                    parsed = datetime.fromisoformat(decided_at.replace("Z", "+00:00"))
                except ValueError:
                    continue
                candidates.append((parsed, index, record))
            if not candidates:
                continue
            latest = max(candidates, key=lambda item: (item[0], item[1]))[2]
            valid_until = latest.get("valid_until")
            if not valid_until:
                continue
            try:
                deadline = datetime.fromisoformat(str(valid_until).replace("Z", "+00:00"))
            except ValueError:
                errors.append({
                    "code": "SDD_STALE_GATE",
                    "path": f"decisions.{gate}.valid_until",
                    "message": f"valid_until ilegível na decisão do gate {gate}: reavaliação obrigatória",
                })
                continue
            if deadline.tzinfo is None:
                deadline = deadline.replace(tzinfo=timezone.utc)
            if deadline < now_dt:
                errors.append({
                    "code": "SDD_STALE_GATE",
                    "path": f"decisions.{gate}.valid_until",
                    "message": f"decisão do gate {gate} expirou em {valid_until}: reavaliação obrigatória",
                })
        return errors

    def _sdd_authorize_stages(self, item_path: Path, stages: tuple[str, ...]) -> list[dict[str, Any]]:
        """Executa validate_package + adapter.authorize por estágio, com falha fechada.

        CORR-1 (auditoria pós-entrega P1#3): a validação estrutural completa
        (``validate_package`` — dúvidas bloqueantes abertas, cobertura, DAG e
        paths) roda ANTES do ``authorize`` e seus erros SDD_* bloqueiam o
        advance/dispatch tanto quanto os erros de autorização.

        A releitura dos hashes (``load_package``) acontece AQUI, sob o lock do
        work item, para capturar inputs alterados após a decisão do gate
        (frescor verificado no avanço, não só na decisão). Política ativa
        ilegível/inválida vira ``SDD_POLICY_INVALID`` (nunca desativa SDD).
        """
        structural, authorization = self._sdd_stage_errors(item_path, stages)
        return structural + authorization

    def _sdd_stage_errors(
        self, item_path: Path, stages: tuple[str, ...]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Retorna ``(erros_estruturais, erros_autorização)`` para os estágios.

        Em modo legado (sem política ativa) a validação estrutural é computada
        quando o pacote é legível (consumo: ``sdd status``); o enforcement de
        advance/dispatch continua não se aplicando em legado (``_sdd_enforce``
        só roda com política ativa). Autorização inclui a checagem de
        ``valid_until`` na cadeia transitiva (relógio da camada CLI).
        """
        adapter, policy_mod = _sdd_adapter_modules()
        project_root = self._sdd_project_root()
        resolution = resolve_sdd_policy(project_root) if project_root is not None else None
        if resolution is None or resolution.state != "active":
            structural: list[dict[str, Any]] = []
            try:
                package = adapter.load_package(item_path)
            except Exception:
                return [], []
            for stage in stages:
                structural.extend(adapter.validate_package(package, stage))
            return structural, []
        if resolution.errors or not isinstance(resolution.policy, dict):
            detail = "; ".join(resolution.errors) or "política ausente"
            return [], [{"code": "SDD_POLICY_INVALID", "path": "policy", "message": detail}]
        policy = resolution.policy
        policy_errors = policy_mod.validate_policy(policy)
        if policy_errors:
            return [], policy_errors
        try:
            package = adapter.load_package(item_path)
        except Exception as exc:  # SDPPackageError e afins: falha fechada
            return [], [{"code": "SDD_MALFORMED", "path": "sdd/package.json", "message": str(exc)}]
        decisions = self._sdd_load_decisions(item_path)
        structural = []
        authorization: list[dict[str, Any]] = []
        required_gates: set[str] = set()
        for stage in stages:
            # CORR-1 (P1#3): estrutura ANTES de autorização — ambos bloqueiam.
            structural.extend(adapter.validate_package(package, stage))
            required_gates.update(policy_mod.STAGE_GATES.get(stage, ()))
            authorization.extend(policy_mod.authorize(package, stage, decisions, policy))
        # valid_until na cadeia transitiva inteira: um gate antecessor expirado
        # invalida os dependentes tanto quanto um input alterado.
        chain = list(policy_mod.GATE_CHAIN)
        chain_index = {gate: index for index, gate in enumerate(chain)}
        deepest = max((chain_index[gate] for gate in required_gates if gate in chain_index), default=-1)
        relevant = set(chain[: deepest + 1]) | required_gates
        authorization.extend(self._sdd_valid_until_errors(decisions, relevant))
        return structural, authorization

    def _sdd_enforce(self, item_path: Path, stages: tuple[str, ...], action: str) -> None:
        """Bloqueia a operação quando o estágio SDD não está autorizado."""
        if not stages or not self._sdd_active(item_path):
            return
        errors = self._sdd_authorize_stages(item_path, stages)
        if errors:
            raise SquadError(_sdd_error_message(errors, action))

    def _sdd_read_context(self, work_item: Path) -> dict[str, Any] | None:
        """Contexto de LEITURA do pacote SDD para activation_packet.

        Nunca concede autorização de escrita: ela pertence à state machine
        (advance-state) e ao dispatch gerenciado (run-engine) com authorize.
        """
        package_file = work_item / "sdd" / "package.json"
        if not package_file.is_file():
            return None
        try:
            package = json.loads(package_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(package, dict):
            return None
        inputs = package.get("inputs") if isinstance(package.get("inputs"), dict) else {}
        read_context: dict[str, str] = {}
        for key in ("spec", "clarifications", "plan", "tasks"):
            entry = inputs.get(key)
            if isinstance(entry, dict) and isinstance(entry.get("path"), str):
                read_context[f"{key}_path"] = entry["path"]
        if isinstance(package.get("constitution_path"), str):
            read_context["constitution_path"] = package["constitution_path"]
        return {
            "read_context": read_context,
            "write_authorization": "state-machine",
            "note": (
                "activation_packet gera apenas contexto de leitura; autorização de "
                "escrita exige advance-state/run-engine com authorize SDD (T5)."
            ),
        }

    def _sdd_stage_briefing(self, work_item: Path, status: dict[str, Any]) -> dict[str, Any] | None:
        """CORR-2 (passo 8 da auditoria): briefing do estágio atual no packet.

        Usa a MESMA montagem de contexto de ``sdd render``/``sdd run``
        (:meth:`_sdd_briefing` -> ``render_command``), SOMENTE LEITURA —
        ``write_authorization`` permanece ``state-machine``. O estágio vem de
        ``SDD_STATE_SPECKIT_STAGE`` (estado do work item) e a persona de
        ``STAGE_PERSONA``. Sem pacote, projeto legado ou estado sem estágio
        mapeado, nada é adicionado ao packet.

        Falha de render é EXPLÍCITA no packet (nunca omissão silenciosa):
        ``status="blocked"`` em política obrigatória (fail-closed — os
        pré-requisitos precisam ser resolvidos antes do dispatch) e
        ``status="unavailable"`` no uso informativo (``sdd.required: false``).
        Política ativa inválida sob política obrigatória vira
        ``policy_warning`` no payload.
        """
        stage = SDD_STATE_SPECKIT_STAGE.get(str(status.get("state", "")))
        if stage is None:
            return None
        project_root = self._sdd_project_root()
        if project_root is None:
            return None
        resolution = resolve_sdd_policy(project_root)
        if resolution.state != "active":
            return None
        mandatory = sdd_required(project_root)
        payload: dict[str, Any] = {"stage": stage}
        if mandatory and (resolution.errors or not isinstance(resolution.policy, dict)):
            payload["policy_warning"] = "SDD_POLICY_INVALID: " + (
                "; ".join(resolution.errors) or "política ausente"
            )
        try:
            import importlib

            rendering = importlib.import_module("squad_sdd_adapter.rendering")
            payload["mode"] = "mandatory" if mandatory else "informational"
            payload["persona"] = rendering.STAGE_PERSONA[stage]
            adapter, _ = _sdd_adapter_modules()
            package = adapter.load_package(work_item)
            payload["status"] = "ok"
            payload["briefing"] = self._sdd_briefing(work_item, package, stage)
        except Exception as exc:  # pré-requisito ausente / pacote ilegível: falha explícita
            payload["status"] = "blocked" if mandatory else "unavailable"
            payload["error"] = str(exc)
            payload["note"] = (
                "fail-closed: briefing não renderizado — resolva os pré-requisitos "
                "ausentes antes do dispatch"
                if mandatory
                else "informativo: briefing indisponível (uso informativo, sem garantia SDD)"
            )
        return payload

    # ------------------------------------------------------------------
    # CORR-1 (P1#1): família CLI `sdd` — init/status/render/run
    # ------------------------------------------------------------------

    @staticmethod
    def _sdd_require_command(stage: str) -> None:
        """Rejeita estágio Spec Kit desconhecido (fail-closed)."""
        if stage not in SDD_SPECKIT_COMMANDS:
            raise SquadError(
                f"estágio SDD desconhecido: {stage!r}; "
                f"válidos: {', '.join(SDD_SPECKIT_COMMANDS)}"
            )

    def _repair_entry_unlocked(
        self,
        item_path: Path,
        *,
        actor: str | None,
        reason: str | None,
        authorization_ref: str | None,
    ) -> dict[str, Any]:
        """Reconcilia a entrada legada, sem executar a máquina de estados."""
        status_path = item_path / "status.yaml"
        before = status_path.read_bytes()
        before_sha = hashlib.sha256(before).hexdigest()
        status = read_yaml(status_path)
        self._validate(status, "work-item.schema.json")

        if not actor or actor not in self.agent_ids or status.get("owner") != actor:
            raise SquadError("repair-entry exige actor do registry igual ao owner do status")
        if not reason or not reason.strip():
            raise SquadError("repair-entry exige reason não vazio")
        if status.get("risk") in {"medium", "high", "critical"} and not authorization_ref:
            raise SquadError("repair-entry exige authorization_ref para risco médio ou maior")
        if authorization_ref:
            authorization_path = Path(authorization_ref)
            if authorization_path.is_absolute() or ".." in authorization_path.parts:
                raise SquadError("authorization_ref precisa ser relativo e contido no work item")

        item_type = status.get("type")
        if not isinstance(item_type, str):
            raise SquadError("repair-entry exige type válido")
        target_cycle, cycle_def = self._resolve_cycle_entry(item_type)
        declared_cycle = status.get("cycle")
        if declared_cycle is not None and declared_cycle != target_cycle:
            raise SquadError("repair-entry rejeitado: cycle divergente do type")
        target_state = str(cycle_def["entry"])

        evidence_root = item_path / "evaluation" / "cycle-entry-repair"
        # Um evento committed é a única confirmação aceita para no-op.
        if evidence_root.is_dir():
            for manifest_path in sorted(evidence_root.glob("*/manifest.json")):
                try:
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                    event_path = manifest_path.parent / "event.json"
                    event = json.loads(event_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if (
                    manifest.get("operation") == "repair-entry"
                    and manifest.get("target_sha256") == before_sha
                    and event.get("outcome") == "committed"
                ):
                    return {"status": "no-op", "operation_id": manifest.get("operation_id")}

        if status.get("state") != "blueprint":
            raise SquadError("repair-entry aceita somente status legado em blueprint")

        operation_id = "REPAIR-" + hashlib.sha256(
            f"{status['id']}|{before_sha}|{target_cycle}|{target_state}".encode("utf-8")
        ).hexdigest()[:24]
        evidence_dir = evidence_root / operation_id
        evidence_dir.mkdir(parents=True, exist_ok=False)
        backup_path = evidence_dir / "status.yaml.before"
        atomic_write_text(backup_path, before.decode("utf-8"), encoding="utf-8")
        if backup_path.read_bytes() != before:
            raise SquadError("backup de status não confere byte a byte")

        target_status = dict(status)
        target_status["cycle"] = target_cycle
        target_status["state"] = target_state
        target_bytes = yaml.safe_dump(target_status, allow_unicode=True, sort_keys=False).encode("utf-8")
        target_sha = hashlib.sha256(target_bytes).hexdigest()
        manifest = {
            "schema_version": 1,
            "operation": "repair-entry",
            "operation_id": operation_id,
            "work_item_id": status["id"],
            "actor": actor,
            "authorization_ref": authorization_ref,
            "reason": reason,
            "started_at": now(),
            "before_sha256": before_sha,
            "target_sha256": target_sha,
            "backup_ref": str(backup_path.relative_to(item_path)).replace("\\", "/"),
            "evidence_ref": str(evidence_dir.relative_to(item_path)).replace("\\", "/"),
            "source": {"type": item_type, "cycle": declared_cycle, "state": status.get("state")},
            "target": {"cycle": target_cycle, "state": target_state},
            "external_effects": {"gates": False, "dispatch": False, "ado": False, "mcp": False},
        }
        atomic_write_text(
            evidence_dir / "manifest.json",
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        status_replaced = False
        try:
            self._validate(target_status, "work-item.schema.json")
            write_yaml(status_path, target_status)
            status_replaced = True
            ledger_path = self._ledger_path(item_path)
            ledger_before = ledger_path.read_text(encoding="utf-8")
            ledger_line = (
                f"| {now()[:10]} | {actor} | {operation_id} | repair-entry | "
                f"{before_sha} -> {target_sha} | {evidence_dir.relative_to(item_path).as_posix()} | committed |\n"
            )
            atomic_write_text(ledger_path, ledger_before + ledger_line, encoding="utf-8")
            event = {
                "schema_version": 1,
                "operation": "repair-entry",
                "operation_id": operation_id,
                "work_item_id": status["id"],
                "actor": actor,
                "outcome": "committed",
                "completed_at": now(),
                "before_sha256": before_sha,
                "target_sha256": target_sha,
                "external_effects": manifest["external_effects"],
            }
            atomic_write_text(
                evidence_dir / "event.json",
                json.dumps(event, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        except Exception as exc:
            if status_replaced:
                atomic_write_text(status_path, before.decode("utf-8"), encoding="utf-8")
            aborted = {
                **{key: manifest[key] for key in ("schema_version", "operation", "operation_id", "work_item_id", "actor")},
                "outcome": "aborted",
                "completed_at": now(),
                "before_sha256": before_sha,
                "target_sha256": target_sha,
                "failure_code": type(exc).__name__,
                "external_effects": manifest["external_effects"],
            }
            try:
                atomic_write_text(
                    evidence_dir / "event.json",
                    json.dumps(aborted, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
            except Exception as audit_exc:
                raise SquadError(f"RECOVERY_AUDIT_INCOMPLETE: {audit_exc}") from exc
            raise SquadError(f"repair-entry abortado: {exc}") from exc

        return {"status": "committed", "operation_id": operation_id}

    @locked_artifact_mutation
    def sdd_init(
        self,
        item_path: Path,
        constitution_path: Path | str | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        """Cria o esqueleto do pacote SDD (``sdd/package.json`` + documentos).

        Permitido em projeto com ``sdd.required: true`` (o init CRIA o pacote)
        e em projeto legado sem política (o init é o caminho de adoção). Falha
        se o pacote já existir, salvo ``force=True`` (com aviso). Os documentos
        placeholder já são gravados com hashes reais no manifesto; editar um
        documento depois exige atualizar a entrada correspondente em
        ``package.json`` (revision + sha256) — a CLI nunca autoatualiza o
        manifesto, preservando a detecção de adulteração (SDD_STALE_GATE).
        """
        status = read_yaml(item_path / "status.yaml")
        sdd_dir = item_path / "sdd"
        package_file = sdd_dir / "package.json"
        if package_file.is_file() and not force:
            raise SquadError(
                f"pacote SDD já existe: {package_file} (use --force para recriar)"
            )
        if force:
            print(f"WARN sdd_init_force_overwrite: pacote SDD existente será recriado: {package_file}")
        sdd_dir.mkdir(parents=True, exist_ok=True)

        if constitution_path is not None:
            source = Path(constitution_path)
            if not source.is_file():
                raise SquadError(f"constituição informada não encontrada: {source}")
            constitution_bytes = source.read_bytes()
        else:
            constitution_bytes = (
                f"# Constituição do projeto {self.project_name or 'legacy'}\n\n"
                "A preencher pelo comando governado 'constitution' (Spec Kit) ou "
                "recriar o pacote com --constitution <arquivo>.\n"
            ).encode("utf-8")

        contents: dict[str, bytes] = {
            "constitution.md": constitution_bytes,
            "spec.md": f"# Spec — {status['id']}\n\nA preencher pelo comando governado 'specify'.\n".encode("utf-8"),
            "clarifications.yaml": b"questions: []\n",
            "plan.md": f"# Plan — {status['id']}\n\nA preencher pelo comando governado 'plan'.\n".encode("utf-8"),
            "tasks.yaml": b"tasks: []\n",
        }
        created: list[str] = []
        for name, data in contents.items():
            (sdd_dir / name).write_bytes(data)
            created.append(f"sdd/{name}")

        package: dict[str, Any] = {
            "schema_version": 1,
            "project_id": self.project_name or "legacy",
            "work_id": status["id"],
            "constitution_path": "sdd/constitution.md",
            "constitution_sha256": _sdd_sha256_bytes(contents["constitution.md"]),
            "inputs": {
                key: {
                    "path": f"sdd/{name}",
                    "revision": "r0",
                    "sha256": _sdd_sha256_bytes(contents[name]),
                }
                for key, name in SDD_PACKAGE_INPUT_FILES.items()
            },
            "requirements": [],
            "reviews": [],
        }
        self._validate(package, "sdd-package.schema.json")
        package_file.write_text(
            json.dumps(package, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        created.append("sdd/package.json")

        # CORR-3 (Adendo da auditoria): estado persistente dos 7 estágios
        stages_data: dict[str, Any] = {
            "schema_version": 1,
            "work_id": status["id"],
            "stages": {
                stage: {
                    "status": "pending",
                    "persona": SDD_COMMAND_PERSONA[stage],
                    "dispatched_at": None,
                    "completed_at": None,
                    "briefing_sha256": None,
                    "output_refs": [],
                }
                for stage in SDD_SPECKIT_COMMANDS
            },
        }
        self._validate(stages_data, "sdd-stages.schema.json")
        stages_file = sdd_dir / "stages.yaml"
        stages_file.write_text(
            yaml.safe_dump(stages_data, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        created.append("sdd/stages.yaml")

        # Sanidade fail-closed: o esqueleto recém-criado precisa validar no
        # estágio base (planning) antes de a CLI declarar sucesso.
        adapter, _ = _sdd_adapter_modules()
        loaded = adapter.load_package(item_path)
        structural = adapter.validate_package(loaded, "planning")
        if structural:
            raise SquadError(_sdd_error_message(structural, "sdd init"))
        # CORR-2 (P1#4): `sdd init` é o caminho de adoção — em projeto com
        # política ativa obrigatória, grava o registro durável de ativação
        # (se ainda não existir) para que apagar a política depois falhe
        # fechado em vez de rebaixar silenciosamente para legado.
        self._sdd_record_activation_on_adoption()
        return {
            "work_item": status["id"],
            "policy": self._sdd_policy_state(),
            "package": "sdd/package.json",
            "created": created,
        }

    def _sdd_record_activation_on_adoption(self) -> None:
        """CORR-2 (P1#4): grava o registro durável na adoção (`sdd init`).

        Somente quando a política ativa é legível e declara explicitamente
        ``sdd.required: true`` e não há registro anterior. Projeto legado
        (sem política) não ganha registro. Falha fechada: erro ao construir
        o registro (política ilegível/sem policy_version) aborta o `sdd init`.
        """
        project_root = self._sdd_project_root()
        if project_root is None:
            return
        resolution = resolve_sdd_policy(project_root)
        if resolution.state != "active" or not isinstance(resolution.policy, dict):
            return
        sdd_section = resolution.policy.get("sdd")
        if not (isinstance(sdd_section, dict) and sdd_section.get("required") is True):
            return
        if (project_root / SDD_ACTIVATION_REL).is_file():
            return
        record = build_sdd_activation(project_root, project_root / SDD_POLICY_REL)
        self._validate(record, "sdd-activation.schema.json")
        write_sdd_activation(project_root, record)
        print(f"INFO sdd_activation_recorded: {project_root / SDD_ACTIVATION_REL}")

    def sdd_activate(self) -> dict[str, Any]:
        """CORR-2 (P1#4): ativação formal e durável da política SDD do projeto.

        Exige ``.agents_squad/config/sdd-policy.yaml`` presente e válido
        (``validate_policy``) e recusa ativação duplicada. Grava
        ``.agents_squad/config/sdd-activation.yaml`` (contrato
        ``sdd-activation.schema.json``) com timestamp UTC, sha256 da política
        e ``policy_version``: a partir daí, apagar a política falha fechado
        (``SDD_POLICY_INVALID``) em vez de rebaixar silenciosamente para legado.
        """
        project_root = self._sdd_project_root()
        if project_root is None:
            raise SquadError(
                "projeto consumidor não resolvido: informe --project-root ou o marcador "
                ".agents_squad/config/project.yaml"
            )
        policy_path = project_root / SDD_POLICY_REL
        if not policy_path.is_file():
            raise SquadError(
                f"política SDD ausente: {policy_path} — crie o arquivo antes de ativar"
            )
        if (project_root / SDD_ACTIVATION_REL).is_file():
            raise SquadError(
                f"registro de ativação SDD já existe: {project_root / SDD_ACTIVATION_REL} "
                "(use 'sdd deactivate' para arquivar antes de reativar)"
            )
        _, policy_mod = _sdd_adapter_modules()
        policy_errors = policy_mod.validate_policy(read_yaml(policy_path))
        if policy_errors:
            raise SquadError(_sdd_error_message(policy_errors, "sdd activate"))
        record = build_sdd_activation(project_root, policy_path)
        self._validate(record, "sdd-activation.schema.json")
        path = write_sdd_activation(project_root, record)
        print(f"INFO sdd_activated: registro durável gravado em {path}")
        return record

    def sdd_deactivate(self) -> dict[str, Any]:
        """CORR-2 (P1#4): desativação formal — arquiva o registro durável.

        O registro não é apagado: vai para
        ``sdd-activation.archived-<timestamp>.yaml`` (trilha auditável) e a
        confirmação é impressa (eco). Recusa quando não há registro. Se a
        política ainda declarar ``sdd.required: true``, emite WARN: enquanto a
        política existir, o enforcement permanece — a desativação do registro
        apenas remove a proteção fail-closed contra política ausente.
        """
        project_root = self._sdd_project_root()
        if project_root is None:
            raise SquadError(
                "projeto consumidor não resolvido: informe --project-root ou o marcador "
                ".agents_squad/config/project.yaml"
            )
        archive = archive_sdd_activation(project_root)
        if archive is None:
            raise SquadError(
                f"nenhum registro de ativação SDD para arquivar: {project_root / SDD_ACTIVATION_REL}"
            )
        print(f"INFO sdd_deactivated: registro arquivado em {archive}")
        if (project_root / SDD_POLICY_REL).is_file() and sdd_required(project_root):
            print(
                f"WARN sdd_deactivate_policy_active: a política {project_root / SDD_POLICY_REL} "
                "ainda declara sdd.required: true — o enforcement SDD permanece enquanto "
                "a política existir; revise a política para desativar o requisito."
            )
        return {"archived_to": archive.name}

    def sdd_status(self, item: Path | str) -> dict[str, Any]:
        """Relatório de operação do pacote SDD: validade, dúvidas bloqueantes,
        decisões de gate e staleness — JSON machine-readable + texto humano."""
        item_path = self._item(item)
        status = read_yaml(item_path / "status.yaml")
        adapter, policy_mod = _sdd_adapter_modules()
        report: dict[str, Any] = {
            "work_item": status["id"],
            "state": status.get("state"),
            "policy": self._sdd_policy_state(),
            "package_present": (item_path / "sdd" / "package.json").is_file(),
            "valid": False,
            "authorized": None,
            "stages": {},
            "open_blocking_questions": [],
            "gate_decisions": [],
            "staleness": [],
            "human": [],
        }
        human = report["human"]
        if not report["package_present"]:
            human.append("pacote SDD ausente (sdd/package.json); execute 'sdd init'")
            return report
        try:
            package = adapter.load_package(item_path)
        except Exception as exc:  # SDPPackageError e afins
            report["load_error"] = str(exc)
            human.append(f"pacote SDD ilegível: {exc}")
            return report

        cycle_def = self.cycles.get("cycles", {}).get(self._cycle_name_for_status(status), {})
        stages = self._sdd_stages_for_state(cycle_def, status.get("state", ""))
        decisions = self._sdd_load_decisions(item_path)
        for stage in stages:
            structural = list(adapter.validate_package(package, stage))
            if report["policy"] == "active":
                resolution = resolve_sdd_policy(self._sdd_project_root())  # type: ignore[arg-type]
                if (
                    resolution.state == "active"
                    and isinstance(resolution.policy, dict)
                    and not policy_mod.validate_policy(resolution.policy)
                ):
                    authorization = list(
                        policy_mod.authorize(package, stage, decisions, resolution.policy)
                    )
                else:
                    authorization = [{
                        "code": "SDD_POLICY_INVALID",
                        "path": "policy",
                        "message": "política ativa inválida (falha fechada)",
                    }]
            else:
                authorization = []
            report["stages"][stage] = {
                "structural_errors": structural,
                "authorization_errors": authorization,
                "valid": not structural and not authorization,
            }
            for error in (*structural, *authorization):
                if error.get("code") == "SDD_OPEN_QUESTION":
                    report["open_blocking_questions"].append(error)
        # "valid" = validade estrutural (validate_package); "authorized" =
        # ausência de erros de autorização (None em modo legado e em política
        # com sdd.required: false — CORR-2/P1#5: sem garantia SDD, relatório
        # apenas informativo; authorize devolve vazio nesses modos).
        report["valid"] = all(
            not entry["structural_errors"] for entry in report["stages"].values()
        )
        mandatory = False
        if report["policy"] == "active":
            mandatory = sdd_required(self._sdd_project_root())  # type: ignore[arg-type]
        if report["policy"] == "active" and mandatory:
            report["authorized"] = bool(report["stages"]) and all(
                not entry["authorization_errors"] for entry in report["stages"].values()
            )

        # Staleness: hash declarado (package.json) vs. hash real (conteúdo).
        inputs = package.get("inputs") if isinstance(package.get("inputs"), dict) else {}
        documents = package.get("documents") if isinstance(package.get("documents"), dict) else {}
        for key in SDD_PACKAGE_INPUT_FILES:
            entry = inputs.get(key)
            declared = entry.get("sha256") if isinstance(entry, dict) else None
            document = documents.get(key)
            real = document.get("sha256") if isinstance(document, dict) else None
            if declared != real:
                report["staleness"].append({
                    "document": key,
                    "declared_sha256": declared,
                    "real_sha256": real,
                })
        document = documents.get("constitution")
        real_const = document.get("sha256") if isinstance(document, dict) else None
        if package.get("constitution_sha256") != real_const:
            report["staleness"].append({
                "document": "constitution",
                "declared_sha256": package.get("constitution_sha256"),
                "real_sha256": real_const,
            })
        for record in decisions:
            report["gate_decisions"].append({
                "gate_id": record.get("gate_id"),
                "decision": record.get("decision"),
                "decider": record.get("decider"),
                "decided_at": record.get("decided_at"),
            })

        human.append(
            f"estado: {status.get('state')} · política: {report['policy']} · "
            f"estágios: {', '.join(stages) if stages else '(nenhum para o estado)'}"
        )
        human.append(f"pacote estruturalmente válido: {'sim' if report['valid'] else 'não'}")
        if report["policy"] == "active" and mandatory:
            human.append(
                f"autorizado para o estado: "
                f"{'sim' if report['authorized'] else 'não'}"
            )
        elif report["policy"] == "active":
            human.append(
                "sdd.required: false: SDD não obrigatório — pacote reportado apenas "
                "informativamente (sem garantia SDD)"
            )
        else:
            human.append("modo legado: sem garantia SDD (authorize não se aplica)")
        if report["open_blocking_questions"]:
            human.append(
                f"dúvidas bloqueantes abertas: {len(report['open_blocking_questions'])}"
            )
        if report["staleness"]:
            human.append(
                "staleness (manifesto diverge do conteúdo): "
                + ", ".join(entry["document"] for entry in report["staleness"])
            )
        human.append(f"decisões de gate registradas: {len(report['gate_decisions'])}")

        # CORR-3: progresso da cadeia de estágios persistida em sdd/stages.yaml
        stages_file = item_path / "sdd" / "stages.yaml"
        if stages_file.is_file():
            try:
                st_data = read_yaml(stages_file)
                st_map = st_data.get("stages", {})
                report["stages"] = st_map
                cur_stage = "completed"
                for s_name in SDD_SPECKIT_COMMANDS:
                    if st_map.get(s_name, {}).get("status") != "completed":
                        cur_stage = s_name
                        break
                report["current_stage"] = cur_stage
                human.append(f"estágio Spec Kit atual: {cur_stage}")
            except Exception:
                report["current_stage"] = None
        else:
            report["current_stage"] = None

        return report

    def _sdd_briefing(self, item_path: Path, package: dict, stage: str) -> str:
        """Monta o contexto real do work item e delega a render_command (T3).

        Evidências de gate vêm das decisões aprovadas mais recentes em
        gate-decisions/; caminhos vêm do manifesto do pacote. Contexto
        ausente levanta SquadError com a lista do que falta (fail-closed).
        """
        import importlib

        rendering = importlib.import_module("squad_sdd_adapter.rendering")

        def _latest_evidence(gate: str) -> str | None:
            candidates: list[tuple[datetime, int, str]] = []
            for index, record in enumerate(self._sdd_load_decisions(item_path)):
                if record.get("gate_id") != gate or record.get("decision") != "approved":
                    continue
                evidence = record.get("evidence")
                if not isinstance(evidence, list) or not evidence:
                    continue
                decided_at = record.get("decided_at")
                try:
                    parsed = datetime.fromisoformat(str(decided_at).replace("Z", "+00:00"))
                except ValueError:
                    parsed = datetime.min.replace(tzinfo=timezone.utc)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                candidates.append((parsed, index, ", ".join(str(entry) for entry in evidence)))
            if not candidates:
                return None
            return max(candidates, key=lambda entry: (entry[0], entry[1]))[2]

        inputs = package.get("inputs") if isinstance(package.get("inputs"), dict) else {}

        def _input_path(key: str) -> str | None:
            entry = inputs.get(key)
            return entry.get("path") if isinstance(entry, dict) else None

        context = {
            "work_id": package.get("work_id"),
            "project_id": package.get("project_id"),
            "spec_path": _input_path("spec"),
            "clarifications_path": _input_path("clarifications"),
            "plan_path": _input_path("plan"),
            "tasks_path": _input_path("tasks"),
            "constitution_path": package.get("constitution_path"),
            "g1_evidence": _latest_evidence("G1-product"),
            "g2_evidence": _latest_evidence("G2-design"),
            "g3_evidence": _latest_evidence("G3-readiness"),
        }
        try:
            return rendering.render_command(stage, context)
        except ValueError as exc:
            raise SquadError(str(exc)) from exc

    def sdd_render(self, item: Path | str, stage: str) -> str:
        """Renderiza o briefing governado do estágio (somente leitura).

        Falha fechada listando pré-requisitos ausentes (contexto obrigatório
        do contrato REQUIRED_CONTEXT do adapter). Autorização de gates é
        responsabilidade de ``sdd_run`` e da state machine.
        """
        self._sdd_require_command(stage)
        item_path = self._item(item)
        adapter, _ = _sdd_adapter_modules()
        try:
            package = adapter.load_package(item_path)
        except Exception as exc:  # SDPPackageError e afins
            raise SquadError(f"SDD_MALFORMED: pacote SDD ilegível: {exc}") from exc
        return self._sdd_briefing(item_path, package, stage)

    def _sdd_check_stage_order(self, item_path: Path, stage: str) -> dict[str, Any]:
        """Garante a ordem sequencial dos estágios Spec Kit (CORR-3).

        Ordem: constitution -> specify -> clarify -> plan -> tasks -> analyze -> implement.
        Para executar o estágio S, todos os estágios anteriores devem estar com status 'completed'
        no sdd/stages.yaml. Se stages.yaml não existir, cria o registro inicial.
        """
        stages_file = item_path / "sdd" / "stages.yaml"
        if not stages_file.is_file():
            status = read_yaml(item_path / "status.yaml")
            stages_data: dict[str, Any] = {
                "schema_version": 1,
                "work_id": status.get("id", item_path.name),
                "stages": {
                    st: {
                        "status": "pending",
                        "persona": SDD_COMMAND_PERSONA[st],
                        "dispatched_at": None,
                        "completed_at": None,
                        "briefing_sha256": None,
                        "output_refs": [],
                    }
                    for st in SDD_SPECKIT_COMMANDS
                },
            }
            self._validate(stages_data, "sdd-stages.schema.json")
            stages_file.write_text(
                yaml.safe_dump(stages_data, allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )
        else:
            stages_data = read_yaml(stages_file)
            self._validate(stages_data, "sdd-stages.schema.json")

        stages = stages_data.get("stages", {})
        idx = SDD_SPECKIT_COMMANDS.index(stage)
        for prev in SDD_SPECKIT_COMMANDS[:idx]:
            prev_status = stages.get(prev, {}).get("status")
            if prev_status != "completed":
                raise SquadError(
                    f"SDD_OUT_OF_ORDER: estágio '{stage}' não pode ser executado; "
                    f"predecessor obrigatório '{prev}' está '{prev_status}' (exige 'completed')"
                )
        return stages_data

    def sdd_run(
        self,
        item: Path | str,
        stage: str,
        *,
        dispatch: bool = True,
    ) -> dict[str, Any]:
        """Executa validate -> authorize -> render -> dispatch para o estágio Spec Kit.

        CORR-3 (Adendo da auditoria):
        1. Valida a ordem sequencial persistida em sdd/stages.yaml (fail-closed).
        2. Executa validação estrutural completa (validate_package) e autorização (authorize).
        3. Monta o briefing governado com o contexto real do work item.
        4. Enfileira uma solicitação durável em sdd/dispatch e registra o estágio
           como ``queued``. Isto é despacho para a outbox, não execução da persona.
        5. Um consumidor externo deve fazer claim e ack com resultado material; só
           então o estágio passa para ``dispatched`` e pode ser concluído.
        """
        self._sdd_require_command(stage)
        item_path = self._item(item)
        stages_data = self._sdd_check_stage_order(item_path, stage)
        current_stage_status = stages_data["stages"][stage].get("status")
        if current_stage_status in {"queued", "dispatched", "completed"}:
            raise SquadError(
                f"SDD_STAGE_ALREADY_ADVANCED: estágio '{stage}' está '{current_stage_status}' e não pode ser redespachado"
            )

        adapter, _ = _sdd_adapter_modules()
        try:
            package = adapter.load_package(item_path)
        except Exception as exc:  # SDPPackageError e afins
            raise SquadError(f"SDD_MALFORMED: pacote SDD ilegível: {exc}") from exc
        structural = list(adapter.validate_package(package, SDD_COMMAND_VALIDATE_STAGE[stage]))
        if structural:
            raise SquadError(_sdd_error_message(structural, f"sdd run {stage}"))
        authorize_stage = SDD_COMMAND_AUTHORIZE_STAGE[stage]
        if authorize_stage is not None:
            authorization = self._sdd_authorize_stages(item_path, (authorize_stage,))
            if authorization:
                raise SquadError(_sdd_error_message(authorization, f"sdd run {stage}"))

        briefing = self._sdd_briefing(item_path, package, stage)
        persona = SDD_COMMAND_PERSONA[stage]
        briefing_hash = hashlib.sha256(briefing.encode("utf-8")).hexdigest()
        now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        dispatch_info: dict[str, Any]
        if dispatch:
            packet = self.activation_packet(persona, item=item_path)
            dispatch_payload = {
                "work_id": item_path.name,
                "stage": stage,
                "persona": persona,
                "briefing": briefing,
                "briefing_sha256": briefing_hash,
                "activation_packet": packet,
            }
            try:
                receipt = self.sdd_dispatcher.enqueue(item_path / "sdd" / "dispatch", dispatch_payload)
                self.sdd_dispatcher.verify(item_path / "sdd" / "dispatch", receipt, expected=dispatch_payload)

                # Persistência do receipt de execução/despacho em disco no work item: sdd/receipts/<stage>-receipt.json
                receipts_dir = item_path / "sdd" / "receipts"
                receipts_dir.mkdir(parents=True, exist_ok=True)
                stage_receipt_file = receipts_dir / f"{stage}-receipt.json"
                stage_receipt_content = {
                    "receipt_id": receipt["receipt_id"],
                    "stage": stage,
                    "persona": persona,
                    "work_id": item_path.name,
                    "dispatched_at": now_iso,
                    "status": "delivered",
                    "briefing_sha256": briefing_hash,
                    "request_ref": receipt["request_ref"],
                    "request_sha256": receipt["request_sha256"],
                    "payload": {
                        "work_id": item_path.name,
                        "stage": stage,
                        "persona": persona,
                        "briefing_sha256": briefing_hash,
                    },
                }
                temp_rcpt = stage_receipt_file.with_name(f".{stage_receipt_file.name}.{uuid.uuid4().hex}.tmp")
                temp_rcpt.write_text(json.dumps(stage_receipt_content, indent=2, ensure_ascii=False), encoding="utf-8")
                temp_rcpt.replace(stage_receipt_file)

                # Persistência do despacho no banco SQLite LocalAgentDB (fail-closed)
                db = LocalAgentDB(
                    db_path=self._db_path(),
                    project_id=self.project_name or "legacy",
                    allow_legacy=self.project_name is None,
                )
                db.log_trajectory(
                    benchmark_name=f"sdd_stage_{stage}",
                    agent_id=persona,
                    task_id=item_path.name,
                    status="dispatched",
                    steps_count=1,
                    tool_calls_count=0,
                    duration_seconds=0.0,
                    details={
                        "briefing_sha256": briefing_hash,
                        "stage": stage,
                        "next_gate": SDD_COMMAND_NEXT_GATE[stage],
                        "receipt_id": receipt["receipt_id"],
                        "receipt_ref": f"receipts/{stage}-receipt.json",
                    },
                )
            except SquadError:
                raise
            except Exception as exc:
                raise SquadError(
                    f"SDD_DISPATCH_FAILURE: SDD_DISPATCH_FAILED: falha ao persistir despacho ou receipt: {exc}"
                ) from exc

            # Aceitação na outbox não é execução. A máquina de estados só avança
            # para ``dispatched`` após um consumidor persistente emitir o receipt
            # de execução em sdd_dispatch_ack.
            stages_data["stages"][stage]["status"] = "queued"
            stages_data["stages"][stage]["dispatched_at"] = now_iso
            stages_data["stages"][stage]["briefing_sha256"] = briefing_hash
            stages_data["stages"][stage]["dispatch_receipt"] = receipt
            self._validate(stages_data, "sdd-stages.schema.json")
            (item_path / "sdd" / "stages.yaml").write_text(
                yaml.safe_dump(stages_data, allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )

            dispatch_info = {
                "dispatched": False,
                "queued": True,
                "persona": persona,
                "packet": packet,
                "dispatched_at": now_iso,
                "briefing_sha256": briefing_hash,
                "receipt": receipt,
            }
        else:
            dispatch_info = {
                "dispatched": False,
                "reason": "dispatch=False solicitado (briefing gerado em modo somente-leitura; estágio permanece no estado anterior)",
            }

        return {
            "stage": stage,
            "persona": persona,
            "next_gate": SDD_COMMAND_NEXT_GATE[stage],
            "briefing": briefing,
            "dispatch": dispatch_info,
        }

    @locked_artifact_mutation
    def sdd_dispatch_claim(self, item_path: Path, stage: str, consumer: str) -> dict[str, Any]:
        self._sdd_require_command(stage)
        data = read_yaml(item_path / "sdd" / "stages.yaml")
        record = data["stages"].get(stage, {})
        if record.get("status") != "queued":
            raise SquadError(f"SDD_NOT_QUEUED: estágio '{stage}' não está queued")
        try:
            return self.sdd_dispatcher.claim(
                item_path / "sdd" / "dispatch", record["dispatch_receipt"]["receipt_id"], consumer
            )
        except Exception as exc:
            raise SquadError(f"SDD_DISPATCH_CLAIM_FAILED: {exc}") from exc

    @locked_artifact_mutation
    def sdd_dispatch_ack(
        self, item_path: Path, stage: str, consumer: str, result_ref: str
    ) -> dict[str, Any]:
        self._sdd_require_command(stage)
        data = read_yaml(item_path / "sdd" / "stages.yaml")
        record = data["stages"].get(stage, {})
        if record.get("status") != "queued":
            raise SquadError(f"SDD_NOT_QUEUED: estágio '{stage}' não está queued")
        result_path = item_path / result_ref
        if Path(result_ref).is_absolute() or ".." in Path(result_ref).parts or not result_path.is_file():
            raise SquadError("SDD_INVALID_EXECUTION_RESULT: result_ref deve ser arquivo relativo existente no work item")
        result = {"status": "completed", "result_ref": Path(result_ref).as_posix(),
                  "result_sha256": hashlib.sha256(result_path.read_bytes()).hexdigest()}
        try:
            execution = self.sdd_dispatcher.ack(
                item_path / "sdd" / "dispatch", record["dispatch_receipt"]["receipt_id"], consumer, result
            )
        except Exception as exc:
            raise SquadError(f"SDD_DISPATCH_ACK_FAILED: {exc}") from exc
        execution_path = item_path / "sdd" / "receipts" / f"{stage}-execution-receipt.json"
        execution_path.parent.mkdir(parents=True, exist_ok=True)
        temp_execution = execution_path.with_name(f".{execution_path.name}.{uuid.uuid4().hex}.tmp")
        temp_execution.write_text(json.dumps(execution, indent=2, ensure_ascii=False), encoding="utf-8")
        temp_execution.replace(execution_path)
        record["status"] = "dispatched"
        record["execution_receipt"] = execution
        self._validate(data, "sdd-stages.schema.json")
        write_yaml(item_path / "sdd" / "stages.yaml", data)
        return execution

    def _sdd_validate_output_refs(
        self, item_path: Path, stage: str, output_refs: list[str] | None
    ) -> dict[str, str]:
        """Resolve referências sem ambiguidade e exige vínculo exato ao plano."""
        planned: set[str] = set()
        if stage == "implement":
            tasks_file = item_path / "sdd" / "tasks.yaml"
            try:
                tasks_data = yaml.safe_load(tasks_file.read_text(encoding="utf-8")) or {}
                for task in tasks_data.get("tasks", []):
                    if isinstance(task, dict):
                        for value in task.get("paths", []):
                            path = Path(str(value))
                            if path.is_absolute() or ".." in path.parts:
                                raise SquadError(f"SDD_INVALID_TASK_PATH: caminho planejado inválido: {value}")
                            planned.add(path.as_posix())
            except SquadError:
                raise
            except Exception as exc:
                raise SquadError(f"SDD_MALFORMED: não foi possível validar paths de tasks.yaml: {exc}") from exc
            if not planned:
                raise SquadError("SDD_MISSING_TASK_PATHS: implement exige paths explícitos em sdd/tasks.yaml")

        roots = [item_path.resolve(), self.root.resolve()]
        if self.project_root:
            roots.append(self.project_root.resolve())
        hashes: dict[str, str] = {}
        for ref in output_refs or []:
            ref_path = Path(ref)
            normalized = ref_path.as_posix()
            if ref_path.is_absolute():
                raise SquadError(f"SDD_OUTPUT_ABSOLUTE_DISALLOWED: caminho absoluto: {ref}")
            if ".." in ref_path.parts:
                raise SquadError(f"SDD_OUTPUT_TRAVERSAL: caminho contém traversal: {ref}")
            if stage == "implement" and normalized not in planned:
                raise SquadError(
                    f"SDD_OUTPUT_NOT_IN_TASKS: '{ref}' não corresponde exatamente a paths de tasks.yaml ({sorted(planned)})"
                )

            matches = [root / ref_path for root in roots if (root / ref_path).is_file()]
            unique = {candidate.resolve(): candidate for candidate in matches}
            if not unique:
                raise SquadError(f"SDD_OUTPUT_NOT_FOUND: arquivo referenciado em output_refs não existe: {ref}")
            if len(unique) > 1:
                raise SquadError(f"SDD_OUTPUT_AMBIGUOUS: referência existe em mais de uma raiz: {ref}")
            resolved, candidate = next(iter(unique.items()))
            if candidate.is_symlink():
                raise SquadError(f"SDD_OUTPUT_SYMLINK_DISALLOWED: output_ref não pode ser symlink: {ref}")
            if not any(resolved == root or root in resolved.parents for root in roots):
                raise SquadError(f"SDD_OUTPUT_OUTSIDE_BOUNDARIES: '{ref}' resolve fora das raízes permitidas")
            if candidate.stat().st_size == 0:
                raise SquadError(f"SDD_EMPTY_OUTPUT: arquivo referenciado em output_refs está vazio: {ref}")
            hashes[ref] = hashlib.sha256(candidate.read_bytes()).hexdigest()
        return hashes

    @locked_artifact_mutation
    def sdd_stage_complete(
        self,
        item: Path | str,
        stage: str,
        *,
        output_refs: list[str] | None = None,
    ) -> dict[str, Any]:
        """Valida saídas esperadas do estágio e marca como 'completed' no stages.yaml.

        CORR-3 / CORR-4:
        1. Valida integridade do sdd/stages.yaml e exige status 'dispatched' anterior.
        2. Confere integridade do hash do briefing registrado no despacho.
        3. Valida predecessores sequenciais (ordem estrita).
        4. Valida saídas materiais esperadas para TODOS os 7 estágios:
           - constitution/specify/plan: arquivo existente, não-vazio e sem placeholders.
           - clarify: questions não-vazio, campos obrigatórios e sem blocking aberto.
           - tasks: tasks não-vazio, campos obrigatórios e pontuação Fibonacci (1..8).
           - analyze: relatório de consistência material existente (ou output_refs).
           - implement: output_refs obrigatório com arquivos reais e não-vazios.
        5. Valida e calcula SHA-256 de todas as referências em output_refs (output_hashes).
        6. Atualiza package.json de forma estrita para a entrada correspondente ao estágio.
        7. Registra completion com timestamp UTC, output_refs e output_hashes em sdd/stages.yaml.
        """
        self._sdd_require_command(stage)
        item_path = self._item(item)
        sdd_dir = item_path / "sdd"
        stages_file = sdd_dir / "stages.yaml"
        if not stages_file.is_file():
            raise SquadError(f"sdd/stages.yaml inexistente em {item_path}")

        stages_data = read_yaml(stages_file)
        self._validate(stages_data, "sdd-stages.schema.json")

        stage_record = stages_data["stages"].get(stage, {})
        current_status = stage_record.get("status")
        if current_status != "dispatched":
            raise SquadError(
                f"SDD_NOT_DISPATCHED: estágio '{stage}' precisa estar no estado 'dispatched' "
                f"antes de ser concluído (status atual: '{current_status}'). "
                f"Execute 'sdd run {stage}' primeiro."
            )

        recorded_briefing_hash = stage_record.get("briefing_sha256")
        if not recorded_briefing_hash:
            raise SquadError(
                f"SDD_MISSING_BRIEFING: estágio '{stage}' não possui briefing_sha256 registrado no despacho."
            )

        # Validação estrita do briefing contra stale briefing
        try:
            adapter, _ = _sdd_adapter_modules()
            current_package = adapter.load_package(item_path)
            expected_briefing = self._sdd_briefing(item_path, current_package, stage)
            expected_briefing_hash = hashlib.sha256(expected_briefing.encode("utf-8")).hexdigest()
            if recorded_briefing_hash != expected_briefing_hash:
                raise SquadError(
                    f"SDD_STALE_BRIEFING: briefing do estágio '{stage}' foi alterado após o despacho "
                    f"(hash registrado: {recorded_briefing_hash[:12]}, hash atual: {expected_briefing_hash[:12]}). "
                    f"Execute 'sdd run {stage}' novamente para atualizar o briefing."
                )
        except SquadError:
            raise
        except Exception as exc:
            raise SquadError(
                f"SDD_BRIEFING_VERIFICATION_FAILED: SDD_BRIEFING_VALIDATION_FAILED: não foi possível recalcular o briefing: {exc}"
            ) from exc

        receipt = stage_record.get("dispatch_receipt")
        if not receipt:
            raise SquadError(
                f"SDD_INVALID_DISPATCH_RECEIPT: estágio '{stage}' não possui dispatch_receipt em stages.yaml"
            )
        expected_dispatch = {
            "work_id": item_path.name,
            "stage": stage,
            "persona": stage_record.get("persona"),
            "briefing_sha256": recorded_briefing_hash,
        }
        try:
            self.sdd_dispatcher.verify(
                item_path / "sdd" / "dispatch", receipt, expected=expected_dispatch
            )
            stage_receipt_file = item_path / "sdd" / "receipts" / f"{stage}-receipt.json"
            if not stage_receipt_file.is_file():
                raise SquadError(
                    f"SDD_INVALID_DISPATCH_RECEIPT: receipt em disco ausente: sdd/receipts/{stage}-receipt.json"
                )
            disk_receipt = json.loads(stage_receipt_file.read_text(encoding="utf-8"))
            if (
                disk_receipt.get("receipt_id") != receipt.get("receipt_id")
                or disk_receipt.get("briefing_sha256") != recorded_briefing_hash
            ):
                raise SquadError("SDD_INVALID_DISPATCH_RECEIPT: receipt em disco divergente ou adulterado")
        except SquadError:
            raise
        except Exception as exc:
            raise SquadError(f"SDD_INVALID_DISPATCH_RECEIPT: receipt ausente, inválido ou adulterado: {exc}") from exc

        execution_receipt = stage_record.get("execution_receipt")
        if not execution_receipt:
            raise SquadError(
                f"SDD_MISSING_EXECUTION_RECEIPT: estágio '{stage}' não possui receipt de execução. "
                "Um consumidor deve executar 'sdd dispatch-ack' após produzir o resultado."
            )
        try:
            self.sdd_dispatcher.verify_execution(
                item_path / "sdd" / "dispatch", execution_receipt, expected=expected_dispatch
            )
            execution_file = item_path / "sdd" / "receipts" / f"{stage}-execution-receipt.json"
            if not execution_file.is_file() or json.loads(execution_file.read_text(encoding="utf-8")) != execution_receipt:
                raise SquadError("SDD_INVALID_EXECUTION_RECEIPT: receipt de execução em disco ausente ou divergente")
        except SquadError:
            raise
        except Exception as exc:
            raise SquadError(f"SDD_INVALID_EXECUTION_RECEIPT: receipt de execução inválido: {exc}") from exc

        idx = SDD_SPECKIT_COMMANDS.index(stage)
        for prev in SDD_SPECKIT_COMMANDS[:idx]:
            if stages_data["stages"].get(prev, {}).get("status") != "completed":
                raise SquadError(
                    f"SDD_OUT_OF_ORDER: estágio '{stage}' não pode ser concluído; "
                    f"predecessor '{prev}' não está 'completed'"
                )

        # Validação estrita de outputs esperados por estágio
        if stage == "constitution":
            f = sdd_dir / "constitution.md"
            if not f.is_file():
                raise SquadError("SDD_MISSING_OUTPUT: sdd/constitution.md ausente para conclusão do estágio constitution")
            content = f.read_text(encoding="utf-8").strip()
            if len(content) < 20 or "A preencher pelo comando governado" in content:
                raise SquadError("SDD_INVALID_OUTPUT: sdd/constitution.md contém apenas placeholder ou conteúdo insuficiente")
        elif stage == "specify":
            f = sdd_dir / "spec.md"
            if not f.is_file():
                raise SquadError("SDD_MISSING_OUTPUT: sdd/spec.md ausente para conclusão do estágio specify")
            content = f.read_text(encoding="utf-8").strip()
            if len(content) < 20 or "A preencher pelo comando governado" in content:
                raise SquadError("SDD_INVALID_OUTPUT: sdd/spec.md contém apenas placeholder ou conteúdo insuficiente")
        elif stage == "clarify":
            f = sdd_dir / "clarifications.yaml"
            if not f.is_file():
                raise SquadError("SDD_MISSING_OUTPUT: sdd/clarifications.yaml ausente para conclusão do estágio clarify")
            try:
                clarif = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
            except Exception as exc:
                raise SquadError(f"SDD_MALFORMED: sdd/clarifications.yaml inválido: {exc}") from exc
            questions = clarif.get("questions")
            if not isinstance(questions, list) or len(questions) == 0:
                raise SquadError("SDD_EMPTY_QUESTIONS: sdd/clarifications.yaml deve conter ao menos uma questão analisada")
            for q in questions:
                if not isinstance(q, dict):
                    raise SquadError("SDD_MALFORMED: itens de 'questions' em clarify devem ser mapeamentos")
                for req_k in ("id", "question", "severity", "status"):
                    if req_k not in q:
                        raise SquadError(f"SDD_MALFORMED: questão {q.get('id', '?')} não possui campo obrigatório '{req_k}'")
            open_blocking = [
                q.get("id", "sem-id")
                for q in questions
                if q.get("severity") == "blocking"
                and q.get("status") == "open"
            ]
            if open_blocking:
                raise SquadError(
                    f"SDD_OPEN_QUESTION: estágio clarify possui dúvidas bloqueantes abertas: {', '.join(open_blocking)}"
                )
        elif stage == "plan":
            f = sdd_dir / "plan.md"
            if not f.is_file():
                raise SquadError("SDD_MISSING_OUTPUT: sdd/plan.md ausente para conclusão do estágio plan")
            content = f.read_text(encoding="utf-8").strip()
            if len(content) < 20 or "A preencher pelo comando governado" in content:
                raise SquadError("SDD_INVALID_OUTPUT: sdd/plan.md contém apenas placeholder ou conteúdo insuficiente")
        elif stage == "tasks":
            f = sdd_dir / "tasks.yaml"
            if not f.is_file():
                raise SquadError("SDD_MISSING_OUTPUT: sdd/tasks.yaml ausente para conclusão do estágio tasks")
            try:
                tasks_data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
            except Exception as exc:
                raise SquadError(f"SDD_MALFORMED: sdd/tasks.yaml inválido: {exc}") from exc
            tasks_list = tasks_data.get("tasks")
            if not isinstance(tasks_list, list) or len(tasks_list) == 0:
                raise SquadError("SDD_EMPTY_TASKS: sdd/tasks.yaml deve conter ao menos uma tarefa governada")
            for t in tasks_list:
                if not isinstance(t, dict):
                    raise SquadError("SDD_MALFORMED: itens de 'tasks' devem ser mapeamentos")
                if "id" not in t or "owner" not in t:
                    raise SquadError(f"SDD_MALFORMED: tarefa {t.get('id', '?')} deve conter ao menos 'id' e 'owner'")
                if "points" in t and t["points"] not in (1, 2, 3, 5, 8):
                    raise SquadError(f"SDD_MALFORMED: tarefa {t['id']} possui Story Points fora da escala Fibonacci (1, 2, 3, 5, 8): {t['points']}")
        elif stage == "analyze":
            analysis_file = sdd_dir / "analysis.md"
            alt_analysis = item_path / "analysis"
            has_analysis_doc = (
                (analysis_file.is_file() and len(analysis_file.read_text(encoding="utf-8").strip()) >= 20)
                or (alt_analysis.is_dir() and any(p.is_file() and p.stat().st_size >= 20 for p in alt_analysis.rglob("*.md")))
            )
            if not has_analysis_doc and not output_refs:
                raise SquadError(
                    "SDD_MISSING_OUTPUT: estágio 'analyze' exige resultado material "
                    "(sdd/analysis.md, analysis/*.md ou output_refs não vazio)"
                )
        elif stage == "implement":
            if not output_refs or len(output_refs) == 0:
                raise SquadError(
                    "SDD_MISSING_OUTPUT: estágio 'implement' exige output_refs não vazio referenciando código/testes"
                )

        output_hashes = self._sdd_validate_output_refs(item_path, stage, output_refs)

        # Atualiza package.json estritamente para o artefato do estágio concluído
        package_file = sdd_dir / "package.json"
        if package_file.is_file():
            pkg = json.loads(package_file.read_text(encoding="utf-8"))
            if stage == "constitution":
                const_file = sdd_dir / "constitution.md"
                if const_file.is_file():
                    pkg["constitution_sha256"] = hashlib.sha256(const_file.read_bytes()).hexdigest()
            elif stage in ("specify", "clarify", "plan", "tasks"):
                key_map = {
                    "specify": "spec",
                    "clarify": "clarifications",
                    "plan": "plan",
                    "tasks": "tasks",
                }
                key = key_map[stage]
                filename = SDD_PACKAGE_INPUT_FILES[key]
                doc_file = sdd_dir / filename
                if doc_file.is_file() and key in pkg.get("inputs", {}):
                    cur_sha = hashlib.sha256(doc_file.read_bytes()).hexdigest()
                    old_sha = pkg["inputs"][key].get("sha256")
                    if cur_sha != old_sha:
                        pkg["inputs"][key]["sha256"] = cur_sha
                        old_rev = str(pkg["inputs"][key].get("revision", "r0"))
                        try:
                            rev_num = int(old_rev.lstrip("r")) + 1
                            pkg["inputs"][key]["revision"] = f"r{rev_num}"
                        except ValueError:
                            pkg["inputs"][key]["revision"] = f"{old_rev}.1"
            self._validate(pkg, "sdd-package.schema.json")
            package_file.write_text(json.dumps(pkg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        # Atualiza stages.yaml
        now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        stages_data["stages"][stage]["status"] = "completed"
        stages_data["stages"][stage]["completed_at"] = now_iso
        stages_data["stages"][stage]["output_refs"] = output_refs or []
        stages_data["stages"][stage]["output_hashes"] = output_hashes
        self._validate(stages_data, "sdd-stages.schema.json")
        stages_file.write_text(
            yaml.safe_dump(stages_data, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )

        return {
            "work_item": stages_data.get("work_id", item_path.name),
            "stage": stage,
            "status": "completed",
            "completed_at": now_iso,
            "output_refs": output_refs or [],
            "output_hashes": output_hashes,
            "next_stage": SDD_SPECKIT_COMMANDS[idx + 1] if idx + 1 < len(SDD_SPECKIT_COMMANDS) else "none",
        }

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
        if next_gate is not None:
            next_gate = self.gate_aliases.get(next_gate, next_gate)
            if next_gate not in self.gate_ids:
                raise SquadError(f"gate desconhecido: {next_gate}")
        for reference in artifacts:
            self._item_reference(item_path, reference, "artefato")
        for reference in evidence:
            self._item_reference(item_path, reference, "evidência")
        if isinstance(memory_delta, str) and (memory_delta.endswith(".yaml") or "/" in memory_delta or "\\" in memory_delta):
            self._item_reference(item_path, memory_delta, "delta de memória")

        status = read_yaml(item_path / "status.yaml")
        seq = len(list((item_path / "handoffs").glob("HANDOFF-*.yaml"))) + 1
        value = {
            "schema_version": 2,
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
            "sod_snapshot": {
                "risk": status["risk"],
                "author": sender,
                "reviewer": recipient if recipient != sender else None,
                "approver": None,
                "executor": None,
                "independence_checked": recipient != sender,
            },
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
        self.validate_sod_snapshot(value["sod_snapshot"])
        self._validate(value, "handoff.schema.json")
        write_yaml(item_path / "handoffs" / f"{value['id']}.yaml", value)

        # Disparo reativo do ContinuousTriggerEngine se habilitado
        try:
            cfg = self.workflow.get("continuous_engine", {})
            if cfg.get("enabled", False):
                from continuous_trigger_engine import (
                    EVENT_HANDOFF_CREATED,
                    ContinuousTriggerEngine,
                )

                engine = ContinuousTriggerEngine(self)
                engine.emit_event(
                    EVENT_HANDOFF_CREATED,
                    status["id"],
                    {
                        "handoff_id": value["id"],
                        "from": sender,
                        "to": recipient,
                        "next_gate": next_gate,
                    },
                )
        except Exception as exc:
            logger.debug("ContinuousTriggerEngine dispatch gracefully skipped: %s", exc)

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
        author: str | None = None,
        reviewer: str | None = None,
    ) -> dict[str, Any]:
        """Avalia e emite formalmente uma decisão de gate (G1 a G6).

        Com a política SDD ativa e pacote SDD no item (T5), a decisão de gate
        SDD-vinculado (G1/G2/G3) registra ``input_hashes`` (hash real dos
        documentos no momento da decisão), ``policy_version`` e exige
        ``author``/``reviewer`` distintos (segregação de papéis — MINOR-1 da
        auditoria T4). Campos aditivos no contrato gate-decision.schema.json.
        """
        item_path = self._item(item)
        gate_id = self.gate_aliases.get(gate_id, gate_id)
        if gate_id not in self.gate_ids:
            raise SquadError(f"gate desconhecido: {gate_id}")
        if not decider:
            raise SquadError("decisor é obrigatório")
        if decider not in self.agent_ids:
            raise SquadError(
                f"decisor desconhecido: {decider}; use um id do registry sem prefixo numérico (ex: delivery-orchestrator)"
            )
        if not criteria:
            raise SquadError("critérios são obrigatórios")
        if not evidence:
            raise SquadError("evidências são obrigatórias")
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
            
        orchestrator_approval = None
        if human_required and human_approved_by in {"00-delivery-orchestrator", "delivery-orchestrator"} and human_evidence:
            orchestrator_approval = {
                "approved_by": human_approved_by,
                "evidence": human_evidence
            }
            human_required = False
            human_approved_by = None
            human_evidence = None

        if human_required and (not human_approved_by or not human_evidence):
            raise SquadError(f"{gate_id} exige aprovação humana e evidência")
        if human_evidence:
            self._item_reference(item_path, human_evidence, "evidência de aprovação humana")
        elif orchestrator_approval:
            self._item_reference(item_path, orchestrator_approval["evidence"], "evidência de aprovação autônoma")

        # Vínculos SDD (T5): computados sob o lock, com hash real (load_package).
        sdd_gate = SDD_CONFIG_GATES.get(gate_id)
        policy_version_value: int | None = None
        input_hashes_value: dict[str, str] | None = None
        valid_until_value: str | None = None
        if self._sdd_active(item_path):
            adapter, policy_mod = _sdd_adapter_modules()
            resolution = resolve_sdd_policy(self._sdd_project_root())  # type: ignore[arg-type]
            if resolution.state != "active" or resolution.errors or not isinstance(resolution.policy, dict):
                detail = "; ".join(resolution.errors) or "política ausente"
                raise SquadError(f"SDD_POLICY_INVALID: {detail}")
            policy = resolution.policy
            policy_errors = policy_mod.validate_policy(policy)
            if policy_errors:
                raise SquadError(_sdd_error_message(policy_errors, "decide-gate policy"))
            policy_version_value = policy.get("policy_version")
            # MINOR-3: TTL de decisão quando a política definir valid_until_days
            # (None sob o contrato atual — ver _sdd_policy_valid_until).
            valid_until_value = self._sdd_policy_valid_until(policy)
            if sdd_gate:
                try:
                    package = adapter.load_package(item_path)
                except Exception as exc:  # SDPPackageError: falha fechada
                    raise SquadError(f"SDD_MALFORMED: pacote SDD ilegível: {exc}") from exc
                documents = package.get("documents", {}) if isinstance(package.get("documents"), dict) else {}
                bound_inputs = policy_mod.GATE_INPUTS.get(sdd_gate, ())
                missing = [
                    doc for doc in bound_inputs
                    if not (documents.get(doc) or {}).get("sha256")
                ]
                if missing:
                    raise SquadError(
                        f"SDD_MISSING_INPUT: documentos ausentes no pacote SDD para {gate_id}: {missing}"
                    )
                input_hashes_value = {doc: documents[doc]["sha256"] for doc in bound_inputs}
                # MINOR-1 (auditoria T4): identidade obrigatória e segregada.
                if not author or not reviewer:
                    raise SquadError(
                        f"decide-gate SDD ({gate_id}) exige author e reviewer para viabilizar "
                        "a verificação de segregação de papéis"
                    )
                if reviewer == decider or reviewer == author:
                    raise SquadError(
                        "segregação de papéis: reviewer não pode ser igual a decider ou author"
                    )

        human_approval = {
            "required": human_required,
            "status": "approved" if human_approved_by else "not_required",
            "approved_by": human_approved_by,
            "evidence": human_evidence,
        }
        results = [{"name": name, "result": result} for name, result in criteria]
        decision = "approved" if all(entry["result"] in {"pass", "not_applicable"} for entry in results) else "changes_requested"
        
        if decision in {"changes_requested", "rejected"}:
            retry_counts = status.get("retry_counts", {})
            current_count = retry_counts.get(gate_id, 0) + 1
            retry_counts[gate_id] = current_count
            status["retry_counts"] = retry_counts
            
            max_retries = self.workflow.get("max_retries_per_check", 2)
            if current_count > max_retries:
                status["state"] = "blocked"
                write_yaml(item_path / "status.yaml", status)
                raise SquadError(f"CIRCUIT_BREAKER_OPEN: limite de {max_retries} tentativas excedido para o portão {gate_id}")
            else:
                write_yaml(item_path / "status.yaml", status)
                
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
        if orchestrator_approval:
            value["orchestrator_approval"] = orchestrator_approval
        if author:
            value["author"] = author
        if reviewer:
            value["reviewer"] = reviewer
        if policy_version_value is not None:
            value["policy_version"] = policy_version_value
        if input_hashes_value is not None:
            value["input_hashes"] = input_hashes_value
        if valid_until_value is not None:
            value["valid_until"] = valid_until_value
        self._validate(value, "gate-decision.schema.json")
        write_yaml(item_path / "gate-decisions" / f"{value['decision_id']}.yaml", value)
        return value

    @locked_artifact_mutation
    def advance_state(
        self,
        item: Path | str,
        *,
        repair_entry: bool = False,
        actor: str | None = None,
        reason: str | None = None,
        authorization_ref: str | None = None,
    ) -> dict[str, Any]:
        """Avança deterministicamente o estado de um work item seguindo o ciclo em config/cycles.yaml.

        Valida se o estado atual exige um gate correspondente e se há decisão aprovada
        em gate-decisions/ antes de permitir o avanço. Atualiza status.yaml de forma
        atômica e retorna o novo estado com os agentes responsáveis.
        """
        item_path = self._item(item)
        if repair_entry:
            return self._repair_entry_unlocked(
                item_path,
                actor=actor,
                reason=reason,
                authorization_ref=authorization_ref,
            )
        status_path = item_path / "status.yaml"
        if not status_path.is_file():
            raise SquadError(f"Arquivo status.yaml não encontrado em {item_path}")
        status = read_yaml(status_path)

        current_state = status.get("state")
        if not current_state:
            raise SquadError(f"Work item {status.get('id')} não possui 'state' definido em status.yaml")
        if current_state == "done":
            raise SquadError(f"Work item {status.get('id')} já está no estado terminal 'done'")

        # 1. Identifica o ciclo correspondente
        cycle_name = self._cycle_name_for_status(status)

        cycles_dict = self.cycles.get("cycles", {})
        if not isinstance(cycles_dict, dict) or cycle_name not in cycles_dict:
            raise SquadError(f"Ciclo '{cycle_name}' não existe em config/cycles.yaml")
        cycle_def = cycles_dict.get(cycle_name, {})
        cycle_states = cycle_def.get("states", [])
        if not cycle_states:
            raise SquadError(f"Ciclo '{cycle_name}' não possui lista de estados definida")

        if current_state not in cycle_states:
            raise SquadError(
                f"Estado atual '{current_state}' não pertence aos estados do ciclo '{cycle_name}': {cycle_states}"
            )

        curr_idx = cycle_states.index(current_state)
        if curr_idx + 1 >= len(cycle_states):
            raise SquadError(f"Não há próximo estado após '{current_state}' no ciclo '{cycle_name}'")
        next_state = cycle_states[curr_idx + 1]

        # 2. Mapeamento de estado para gate obrigatório antes do avanço
        state_to_gate = {
            "blueprint": "G1-product",
            "discovery": "G1-product",
            "product-ready": "G1-product",
            "design": "G2-design",
            "design-ready": "G2-design",
            "scaffolding": "G3-readiness",
            "readiness": "G3-readiness",
            "ready-for-build": "G3-readiness",
            "code-security-review": "G4-code-security",
            "review": "G4-code-security",
            "quality-validation": "G5-quality",
            "validation": "G5-quality",
            "governance-release": "G6-governance-release",
            "ready-for-release": "G6-governance-release",
        }

        gate_bypass = bool(cycle_def.get("gate_bypass", False))
        required_gate = None if gate_bypass else state_to_gate.get(current_state)

        # 2.5 Enforcement SDD (T5): com pacote SDD e política ativa, o avanço
        # exige authorize do adapter (T4) para os estágios do estado — ANTES da
        # checagem genérica, para que os códigos SDD_* sejam surfaced. Qualquer
        # erro aborta SEM alterar estado nem despachar executor. Executa sob o
        # lock do item, com releitura dos hashes (load_package) e escrita
        # atômica subsequente: corrida entre avaliação e avanço é rejeitada
        # (lock) e input alterado após o gate é capturado na releitura.
        if not gate_bypass:
            # MAJOR-2: política ativa + pacote SDD ausente => bloqueia sempre
            # (o contrato de política não define data de ativação para escape).
            package_error = self._sdd_missing_package_error(item_path)
            if package_error:
                raise SquadError(package_error)
            sdd_stages = self._sdd_stages_for_state(cycle_def, current_state)
            self._sdd_enforce(item_path, sdd_stages, f"advance {current_state} -> {next_state}")

        # 3. Validação do gate correspondente em gate-decisions/
        if required_gate:
            canonical_gate = self.gate_aliases.get(required_gate, required_gate)
            valid_gate_names = {
                required_gate.lower(),
                canonical_gate.lower(),
                required_gate.upper(),
                canonical_gate.upper(),
            }
            if required_gate in {"G3-readiness", "GT-design-review"}:
                valid_gate_names.update({"g3-readiness", "gt-design-review", "g3_readiness", "gt_design_review"})
            if required_gate in {"G1-product", "GT-entry"}:
                valid_gate_names.update({"g1-product", "gt-entry", "g1_product", "gt_entry"})

            decisions_dir = item_path / "gate-decisions"
            has_approved_decision = False

            if decisions_dir.is_dir():
                for dec_file in decisions_dir.glob("*.yaml"):
                    try:
                        dec_data = read_yaml(dec_file)
                        dec_gate_id = str(dec_data.get("gate_id", "")).lower()
                        decision_val = str(dec_data.get("decision", "")).lower()
                        file_matches_gate = any(g in dec_file.stem.lower() for g in valid_gate_names)
                        gate_matches = (dec_gate_id in valid_gate_names) or file_matches_gate
                        if gate_matches and decision_val in {"approved", "pass"}:
                            has_approved_decision = True
                            break
                    except Exception:
                        continue

            if not has_approved_decision:
                raise SquadError(
                    f"Avanço de estado bloqueado: o estado '{current_state}' exige a aprovação do gate '{required_gate}'. "
                    f"Nenhuma decisão com status 'approved' encontrada em gate-decisions/."
                )

        # 4. Agentes responsáveis pelo novo estado
        states_cfg = {s["id"]: s for s in self.workflow.get("states", [])}
        next_state_cfg = states_cfg.get(next_state, {})
        next_owner = next_state_cfg.get("owner", status.get("owner", "delivery-orchestrator"))
        collaborators = next_state_cfg.get("collaborators", [])
        selectable = next_state_cfg.get("selectable_agents", [])

        responsible_agents: list[str] = []
        if next_owner and next_owner not in responsible_agents:
            responsible_agents.append(next_owner)
        for col in collaborators:
            if col not in responsible_agents:
                responsible_agents.append(col)

        # Atualiza status.yaml de forma governada
        status["state"] = next_state
        status["owner"] = next_owner
        status["active_agents"] = responsible_agents[:10]
        status["current_gate"] = state_to_gate.get(next_state) if not gate_bypass else None
        status["updated_at"] = now()
        self._validate(status, "work-item.schema.json")
        write_yaml(status_path, status)

        # Sincronizar estado com Azure DevOps se devops_id estiver presente
        devops_id = status.get("devops_id")
        if devops_id:
            try:
                from pathlib import Path as _Path
                import sys as _sys
                _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent / "integrations"))
                from devops_platform_connector import DevOpsPlatformConnector
                connector = DevOpsPlatformConnector(root_path=_Path(__file__).resolve().parents[1])
                if hasattr(connector.client, 'send'):
                    # state_map canônico: estados internos → estados ADO
                    ado_state_map = {
                        "blueprint": "New",
                        "scaffolding": "Active",
                        "implementation": "Active",
                        "code-security-review": "Active",
                        "quality-validation": "Resolved",
                        "governance-release": "Resolved",
                        "done": "Closed",
                    }
                    ado_state = ado_state_map.get(next_state)
                    if ado_state:
                        connector.client.send(
                            "PATCH",
                            f"{connector.client.org}/{connector.client.project}/_apis/wit/workitems/{devops_id}?api-version=7.1",
                            [{"op": "add", "path": "/fields/System.State", "value": ado_state}],
                            content_type="application/json-patch+json",
                        )
                elif hasattr(connector, 'update_item_state'):
                    connector.update_item_state(str(devops_id), next_state)
            except Exception as _e:
                # Falha silenciosa — advance-state local não deve ser bloqueado por falha ADO
                print(f"WARN advance_state_devops_sync_failed: {_e}")
                logger.warning(f"Falha ao sincronizar advance-state com Azure DevOps: {_e}")

        return {
            "work_id": status["id"],
            "previous_state": current_state,
            "state": next_state,
            "owner": next_owner,
            "active_agents": status["active_agents"],
            "current_gate": status.get("current_gate"),
            "responsible_agents": responsible_agents,
            "selectable_agents": selectable,
            "cycle": cycle_name,
        }

    def run_continuous(
        self,
        item: Path | str,
        *,
        max_steps: int = 10,
        dry_run: bool = False,
        reset_circuit_breaker: bool = False,
    ) -> dict[str, Any]:
        """Executa o motor contínuo de gatilhos para o work item."""
        from continuous_trigger_engine import ContinuousTriggerEngine

        engine = ContinuousTriggerEngine(self)
        item_path = self._item(item)
        work_id = item_path.name
        if reset_circuit_breaker:
            engine.reset_circuit_breaker(work_id)
        return engine.run_continuous(work_id, max_steps=max_steps, dry_run=dry_run)

    def check_sizing(
        self, points: int | None = None, item: Path | str | None = None
    ) -> dict[str, Any]:
        """Valida Story Points na escala Fibonacci governada (1, 2, 3, 5, 8).

        Se for > 8, bloqueia formalmente exigindo split pelo 40-agile-coach.
        """
        if points is None:
            if item is None:
                raise SquadError("check-sizing exige informar --points <N> ou --work-item <ID>")
            item_path = self._item(item)
            status = read_yaml(item_path / "status.yaml")
            points = status.get("story_points")
            if points is None:
                raise SquadError(f"Work item {status.get('id')} não possui 'story_points' em status.yaml")

        allowed_points = {1, 2, 3, 5, 8}
        if points in allowed_points:
            return {
                "status": "APPROVED",
                "story_points": points,
                "approved": True,
                "message": f"Story Points ({points}) válido e dentro do limite cognitivo (máx 8 pts).",
            }
        elif points > 8:
            raise SquadError(
                f"Bloqueio de proteção cognitiva: Story Points ({points}) > 8 pontos. "
                "Divisão obrigatória da história pelo 40-agile-coach (Story Split)."
            )
        else:
            raise SquadError(
                f"Story Points inválido: {points}. "
                f"Valores permitidos na escala Fibonacci governada: {sorted(allowed_points)}."
            )

    def _memory_delta_unlocked(
        self,
        item_path: Path,
        author: str,
        statement: str,
        source: str = "handoff",
        kind: str = "fact",
    ) -> int:
        """Persiste um fato estruturado no SQLite (banco/squad.db) e sincroniza com Azure DevOps."""
        if author not in self.agent_ids:
            if "-" in author and author.split("-", 1)[1] in self.agent_ids:
                pass
            else:
                raise SquadError("autor precisa existir no agent-registry")

        status = read_yaml(item_path / "status.yaml")
        db = LocalAgentDB(
            db_path=self._db_path(),
            project_id=self.project_name or "legacy",
            allow_legacy=self.project_name is None,
        )
        fact_id = db.record_memory_fact(
            project_id=db.project_id,
            work_item_id=status["id"],
            author=author,
            kind=kind,
            statement=statement,
            source=source,
            confidence=1.0,
            sensitivity="internal",
        )

        devops_id = status.get("devops_id")
        if devops_id:
            try:
                try:
                    from integrations.devops_platform_connector import DevOpsPlatformConnector
                except ImportError:
                    from pathlib import Path as _Path
                    import sys as _sys
                    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent / "integrations"))
                    from devops_platform_connector import DevOpsPlatformConnector
                connector = DevOpsPlatformConnector(root_path=self.root)

                comment = f"> **Memory Delta: [{author}]** [{kind}]: {statement}"
                if hasattr(connector, "add_work_item_comment"):
                    connector.add_work_item_comment(devops_id, comment)
                elif hasattr(connector.client, "add_work_item_comment"):
                    connector.client.add_work_item_comment(devops_id, comment)
                elif hasattr(connector.client, "send"):
                    connector.client.send(
                        "PATCH",
                        f"{connector.client.org}/{connector.client.project}/_apis/wit/workitems/{devops_id}?api-version=7.1",
                        [{"op": "add", "path": "/fields/System.History", "value": comment}],
                        content_type="application/json-patch+json",
                    )
                elif hasattr(connector.client, "update_item_state"):
                    current_ado_state = status.get("state", "Active")
                    connector.client.update_item_state(str(devops_id), current_ado_state, comment=comment)
                elif hasattr(connector, "update_item_state"):
                    current_ado_state = status.get("state", "Active")
                    connector.update_item_state(str(devops_id), current_ado_state, comment=comment)
            except Exception as _ado_err:
                print(f"WARN memory_delta_devops_sync_failed: {_ado_err}")
                logger.warning(f"Falha ao sincronizar comentário de memória com Azure DevOps: {_ado_err}")

        return fact_id

    @locked_artifact_mutation
    def memory_delta(
        self,
        item: Path | str,
        author: str,
        statement: str,
        source: str = "handoff",
        kind: str = "fact",
    ) -> int:
        """Persiste um fato estruturado no SQLite (banco/squad.db) e sincroniza com Azure DevOps se devops_id presente.

        Retorna o ID do fato registrado.
        """
        item_path = self._item(item)
        return self._memory_delta_unlocked(item_path, author, statement, source=source, kind=kind)

    @locked_artifact_mutation
    def record_memory(
        self,
        item: Path | str,
        author: str,
        statement: str,
        source: str,
        kind: str = "fact",
    ) -> dict[str, Any]:
        """Registra um delta de memória no SQLite, cria arquivo e sincroniza com Azure DevOps."""
        item_path = self._item(item)
        if author not in self.agent_ids:
            if "-" in author and author.split("-", 1)[1] in self.agent_ids:
                pass
            else:
                raise SquadError("autor precisa existir no agent-registry")
        try:
            self._item_reference(item_path, source, "fonte da memória")
        except SquadError:
            pass
        status = read_yaml(item_path / "status.yaml")
        deltas_dir = item_path / "memory/deltas"
        deltas_dir.mkdir(parents=True, exist_ok=True)
        seq = len(list(deltas_dir.glob("MEM-*.yaml"))) + 1
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
        write_yaml(deltas_dir / f"{value['id']}.yaml", value)
        shared = item_path / "memory/shared/summary.md"
        shared.parent.mkdir(parents=True, exist_ok=True)
        if shared.exists():
            shared_content = shared.read_text(encoding="utf-8")
        else:
            shared_content = f"# Memória compartilhada — {status['id']}\n"
        shared_content += f"\n- [{value['id']}] {statement} (fonte: {source})\n"
        atomic_write_text(shared, shared_content, encoding="utf-8")

        # Persistência estruturada via _memory_delta_unlocked
        try:
            fact_id = self._memory_delta_unlocked(item_path, author, statement, source=source, kind=kind)
            value["db_fact_id"] = fact_id
        except Exception as _db_err:
            print(f"WARN record_memory_db_failed: {_db_err}")
            logger.warning(f"Falha ao persistir memory_fact no SQLite: {_db_err}")

        return value

    def discover(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        """Pesquisa somente o catálogo aprovado; intake e quarentena nunca entram."""
        import importlib

        _skill_selector = importlib.import_module("scripts.skill_selector")
        DiscoveredSkill = _skill_selector.DiscoveredSkill
        select_skills_for_task = _skill_selector.select_skills_for_task

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
            try:
                packet["work_item"] = work_item.relative_to(self._work_base()).as_posix()
            except ValueError:
                packet["work_item"] = work_item.name
            memory_root = (
                f"work/{self.project_name}/{status['id']}"
                if self.project_name
                else f"work/{status['id']}"
            )
            memory_manifest = manifest.get("memory") or {
                "private": f"work/<WORK-ID>/memory/agents/{agent}.md",
                "shared": "work/<WORK-ID>/memory/shared/summary.md",
            }
            packet["memory"] = {
                key: value.replace("work/<WORK-ID>", memory_root)
                for key, value in memory_manifest.items()
            }
            # T5: contexto de LEITURA do pacote SDD (quando existente). Nunca
            # concede autorização de escrita — ela pertence à state machine.
            sdd_context = self._sdd_read_context(work_item)
            if sdd_context is not None:
                packet["sdd"] = sdd_context
                # CORR-2 (passo 8): briefing governado do estágio atual +
                # persona (STAGE_PERSONA), mesma montagem de sdd render/run,
                # somente leitura. Falha de render é explícita no packet.
                stage_briefing = self._sdd_stage_briefing(work_item, status)
                if stage_briefing is not None:
                    packet["sdd"]["stage_briefing"] = stage_briefing
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

    def deep_audit(self) -> dict[str, Any]:
        """Varre work/**/status.yaml e valida cada work item real contra os contratos.

        Complementa ``audit()`` (que só cobre catálogo de skills e manifestos):
        aqui é medida a conformidade real de status.yaml/handoffs/gate-decisions
        em todo o portfólio de projetos, e são sinalizadas pastas órfãs sob
        ``work/`` sem nenhum work item governado dentro.
        """
        work_root = self.root / "work"
        conformant: list[str] = []
        non_conformant: dict[str, str] = {}
        if work_root.is_dir():
            for status_path in sorted(work_root.rglob("status.yaml")):
                item_dir = status_path.parent
                rel = item_dir.relative_to(work_root).as_posix()
                try:
                    item_errors = self.validate_work_item(item_dir)
                except SquadError as exc:
                    non_conformant[rel] = str(exc)
                    continue
                if item_errors:
                    non_conformant[rel] = "; ".join(item_errors)
                else:
                    conformant.append(rel)

        orphans: list[str] = []
        if work_root.is_dir():
            for entry in work_root.iterdir():
                if not entry.is_dir() or entry.name == "light":
                    continue
                if not any(True for _ in entry.rglob("status.yaml")):
                    orphans.append(entry.relative_to(work_root).as_posix())

        return {
            "conformant_count": len(conformant),
            "non_conformant_count": len(non_conformant),
            "non_conformant": non_conformant,
            "orphans": orphans,
        }

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
        # CORR-2 (P2/passos 4): vendor governado sob VENDOR_SKILL_SCAN_EXCLUSIONS
        # sai do scan; a exclusão é sempre reportada como linha informativa abaixo.
        excluded_vendor = 0

        def _vendor_excluded(relative_posix: str) -> bool:
            nonlocal excluded_vendor
            for prefix in VENDOR_SKILL_SCAN_EXCLUSIONS:
                if relative_posix.startswith(prefix):
                    excluded_vendor += 1
                    return True
            return False

        for scan_dir in ("skills", "integrations"):
            for path in (self.root / scan_dir).rglob("SKILL.md"):
                posix = path.as_posix()
                if "skills/discovery/intake/" in posix:
                    continue
                if "skills/discovery/quarantine/" in posix:
                    continue
                if "/vendor/" in posix or "/__pycache__/" in posix:
                    continue
                relative_parent = path.parent.relative_to(self.root).as_posix()
                if _vendor_excluded(relative_parent):
                    continue
                actual_paths.add(relative_parent)
        for path in (self.root / "integrations").rglob("*.py"):
            posix = path.as_posix()
            if "/vendor/" in posix or "/__pycache__/" in posix:
                continue
            relative = path.relative_to(self.root).as_posix()
            if path.name != "__init__.py" and not _vendor_excluded(relative):
                actual_paths.add(relative)
        for prefix in VENDOR_SKILL_SCAN_EXCLUSIONS:
            print(
                f"INFO audit: exclusão de vendor '{prefix}' ({excluded_vendor} caminhos) — "
                "regra VENDOR_SKILL_SCAN_EXCLUSIONS; integridade do vendor é atestada por "
                "integrations/spec-kit/upstream/UPSTREAM_FILES.sha256 (verify_snapshot.py), "
                "não pelo catálogo de skills"
            )
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
        except (OSError, ValueError, RuntimeError) as exc:
            # Banco indisponível: alerta explícito em vez de mascarar.
            print(f"WARN track_tokens_db_unavailable: {type(exc).__name__}: {exc}")
            try:
                db_fallback = LocalAgentDB(
                    db_path=self._db_path(),
                    project_id=self.project_name or "legacy",
                    allow_legacy=True,
                )
                db_fallback.record_recovery_event(
                    target_key=f"track_tokens:{status.get('id', 'unknown')}",
                    phase="track-tokens",
                    reason="db_unavailable",
                    action="escalate",
                    attempt=1,
                    backoff_seconds=0.0,
                    error_message=f"{type(exc).__name__}: {exc}",
                    rationale="track_tokens falhou ao gravar no banco",
                )
            except Exception as e:
                logger.debug("track_tokens fallback failed: %s", e)
                raise
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
        """Executa um motor de integração do diretório integrations/ com validação de path.
        Procura em integrations/{engine}.py E integrations/experimental/{engine}.py.

        Dispatch gerenciado (T5): quando o work item tem pacote SDD e o projeto
        ativou a política, o estágio do estado atual precisa estar autorizado
        (adapter.authorize) ANTES de qualquer execução — bugfix/retomada não
        contornam o preflight. Erros SDD_* abortam sem despachar executor.
        """
        if kwargs.get("work_item"):
            try:
                item_path = self._item(kwargs["work_item"])
            except SquadError as exc:
                # MAJOR-1 (revisão T5): fail-closed. Com política SDD ativa,
                # item não resolvível na base governada ABORTA o despacho —
                # nunca prossegue sem preflight. Em modo legado (sem política)
                # preserva a compatibilidade dos demos/usos existentes.
                if self._sdd_policy_state() == "active":
                    raise SquadError(
                        "SDD fail-closed: dispatch bloqueado — work item não resolvível "
                        f"na base governada: {kwargs['work_item']!r}. Resolva o item em "
                        "work/<projeto>/<WORK-ID> ou use a política legada explicitamente."
                    ) from exc
                item_path = None
            if item_path is not None:
                status = read_yaml(item_path / "status.yaml")
                cycle_def = self.cycles.get("cycles", {}).get(self._cycle_name_for_status(status), {})
                if not cycle_def.get("gate_bypass", False):
                    # MAJOR-2: política ativa + pacote SDD ausente => bloqueia.
                    package_error = self._sdd_missing_package_error(item_path)
                    if package_error:
                        raise SquadError(package_error)
                    stages = self._sdd_stages_for_state(cycle_def, status.get("state", ""))
                    self._sdd_enforce(item_path, stages, f"dispatch {engine}")

        engines_dir = (self.root / "integrations").resolve()
        # Check both integrations/ root and integrations/experimental/
        candidates = [
            (engines_dir / f"{engine}.py").resolve(),
            (engines_dir / "experimental" / f"{engine}.py").resolve(),
        ]
        script = None
        for candidate in candidates:
            if candidate.is_file():
                script = candidate
                break
        if script is None:
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

    def index_codebase(self, target_dir: Path | str | None = None) -> dict[str, Any]:
        """Indexa arquivos de código do projeto no banco SQLite."""
        dir_path = Path(target_dir).resolve() if target_dir else self.root
        db = LocalAgentDB(db_path=self._db_path(), project_id=self.project_name or "legacy", allow_legacy=self.project_name is None)
        count = db.index_directory(dir_path)
        return {"indexed_files": count, "directory": str(dir_path), "project_id": db.project_id}

    def query_memory(self, item: Path | str, kind: str | None = None) -> list[dict[str, Any]]:
        """Consulta fatos de memória registrados no SQLite para um work item."""
        item_path = self._item(item)
        status = read_yaml(item_path / "status.yaml")
        db = LocalAgentDB(db_path=self._db_path(), project_id=self.project_name or "legacy", allow_legacy=self.project_name is None)
        return db.get_memory_facts(project_id=db.project_id, work_item_id=status["id"], kind=kind)


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

    migrate = sub.add_parser("migrate-handoffs")
    migrate.add_argument("--work-item", required=True)
    migrate.add_argument("--dry-run", action="store_true")

    reclassify = sub.add_parser(
        "reclassify-work-item",
        help="Reclassifica um work item existente; dry-run por padrão",
    )
    reclassify.add_argument("--work-item", required=True)
    reclassify.add_argument("--from-type", required=True, choices=["evolution"])
    reclassify.add_argument("--to-type", required=True, choices=["epic"])
    reclassify.add_argument(
        "--apply",
        action="store_true",
        help="Aplica a mudança e grava evidência; sem esta flag apenas simula",
    )

    init = sub.add_parser("init-work-item")
    init.add_argument("--id", required=True)
    init.add_argument("--risk", default="medium", choices=["low", "medium", "high", "critical"])
    init.add_argument(
        "--type", dest="item_type",
        choices=["epic", "feature", "story", "task", "bug", "release", "evolution", "study", "spike"],
        default=None,
    )
    init.add_argument("--story-points", type=int, default=None)
    init.add_argument("--parent-id", type=str, default=None)
    init.add_argument("--force", action="store_true")
    init.add_argument("--devops", action="store_true", help="Cria card no Azure DevOps")

    light = sub.add_parser("light-start")
    light.add_argument("--id", required=True)
    light.add_argument("--risk", default="low", choices=["low", "medium"])
    light.add_argument("--objective", default="")

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
    gate.add_argument("--author", help="autor da entrega (obrigatório em decide-gate SDD)")
    gate.add_argument("--reviewer", help="revisor independente (obrigatório em decide-gate SDD)")

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

    audit = sub.add_parser("audit")
    audit.add_argument("--deep", action="store_true",
                        help="Também valida status.yaml/handoffs/gate-decisions de todo work/ (não só o catálogo de skills)")
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

    init_proj = sub.add_parser("init-project")
    init_proj.add_argument("--project-name", required=True)
    init_proj.add_argument("--project-root", required=True, type=Path)
    init_proj.add_argument("--devops", action="store_true")
    init_proj.add_argument("--dry-run", action="store_true")

    adv = sub.add_parser("advance-state")
    adv.add_argument("--work-item", "--item", dest="work_item", required=True)
    adv.add_argument("--repair-entry", action="store_true")
    adv.add_argument("--actor")
    adv.add_argument("--reason")
    adv.add_argument("--authorization-ref")

    run_cont = sub.add_parser(
        "run-continuous", help="Executa o motor contínuo de gatilhos do orquestrador"
    )
    run_cont.add_argument(
        "--work-item", "--item", dest="work_item", required=True, help="ID ou caminho do work item"
    )
    run_cont.add_argument(
        "--max-steps", type=int, default=10, help="Número máximo de passos contínuos"
    )
    run_cont.add_argument(
        "--dry-run", action="store_true", help="Simula os passos sem persistir transições"
    )
    run_cont.add_argument(
        "--reset-circuit-breaker",
        action="store_true",
        help="Rearma o circuit breaker antes de executar",
    )

    sizing = sub.add_parser("check-sizing")
    sizing.add_argument("--points", type=int, default=None)
    sizing.add_argument("--work-item", "--item", dest="work_item", default=None)

    idx = sub.add_parser("index-codebase")
    idx.add_argument("--dir", dest="target_dir", default=None, help="Diretório a ser indexado (padrão: raiz do projeto/squad)")

    qry = sub.add_parser("query-memory")
    qry.add_argument("--work-item", "--item", dest="work_item", required=True, help="ID ou caminho do work item")
    qry.add_argument("--kind", choices=["fact", "decision", "dependency", "risk", "pending"], default=None, help="Tipo de fato a filtrar")

    mem_delta = sub.add_parser("memory-delta")
    mem_delta.add_argument("--work-item", "--item", dest="work_item", required=True, help="ID ou caminho do work item")
    mem_delta.add_argument("--author", required=True, help="Autor/agente do fato")
    mem_delta.add_argument("--statement", required=True, help="Conteúdo do fato")
    mem_delta.add_argument("--source", default="handoff", help="Origem do fato (padrão: handoff)")
    mem_delta.add_argument("--kind", choices=["fact", "decision", "dependency", "risk", "pending"], default="fact", help="Tipo de fato")

    # CORR-1 (P1#1): família Spec Kit — sdd init/status/render/run.
    sdd = sub.add_parser(
        "sdd",
        help="Fluxo Spec Kit governado: init, status, render e run por estágio",
    )
    sdd_sub = sdd.add_subparsers(dest="sdd_command", required=True)
    sdd_init_p = sdd_sub.add_parser("init", help="Cria o esqueleto do pacote SDD no work item")
    sdd_init_p.add_argument("--work-item", required=True)
    sdd_init_p.add_argument(
        "--constitution", type=Path, default=None,
        help="Arquivo de constituição a adotar (copiado para sdd/constitution.md)",
    )
    sdd_init_p.add_argument("--force", action="store_true", help="Recria o pacote existente (com aviso)")
    sdd_status_p = sdd_sub.add_parser("status", help="Validade, dúvidas bloqueantes, gates e staleness")
    sdd_status_p.add_argument("--work-item", required=True)
    sdd_render_p = sdd_sub.add_parser("render", help="Renderiza o briefing governado do estágio")
    sdd_render_p.add_argument("--work-item", required=True)
    sdd_render_p.add_argument("--stage", required=True, help=f"Um de: {', '.join(SDD_SPECKIT_COMMANDS)}")
    sdd_run_p = sdd_sub.add_parser("run", help="validate -> authorize -> render -> dispatch (briefing e ativação gerenciada)")
    sdd_run_p.add_argument("--work-item", required=True)
    sdd_run_p.add_argument("--stage", required=True, help=f"Um de: {', '.join(SDD_SPECKIT_COMMANDS)}")
    sdd_run_p.add_argument("--no-dispatch", dest="dispatch", action="store_false", default=True, help="Valida e gera briefing sem despachar o agente")
    sdd_complete_p = sdd_sub.add_parser("stage-complete", help="Valida saídas da etapa e marca como completed no stages.yaml")
    sdd_complete_p.add_argument("--work-item", required=True)
    sdd_complete_p.add_argument("--stage", required=True, help=f"Um de: {', '.join(SDD_SPECKIT_COMMANDS)}")
    sdd_complete_p.add_argument("--output-refs", nargs="*", default=None, help="Referências aos artefatos gerados")
    sdd_claim_p = sdd_sub.add_parser("dispatch-claim", help="Consumidor persistente reivindica uma solicitação SDD enfileirada")
    sdd_claim_p.add_argument("--work-item", required=True)
    sdd_claim_p.add_argument("--stage", required=True, help=f"Um de: {', '.join(SDD_SPECKIT_COMMANDS)}")
    sdd_claim_p.add_argument("--consumer", required=True, help="Identidade do consumidor que executará a persona")
    sdd_ack_p = sdd_sub.add_parser("dispatch-ack", help="Registra receipt de execução de um consumidor SDD")
    sdd_ack_p.add_argument("--work-item", required=True)
    sdd_ack_p.add_argument("--stage", required=True, help=f"Um de: {', '.join(SDD_SPECKIT_COMMANDS)}")
    sdd_ack_p.add_argument("--consumer", required=True, help="Identidade do consumidor que executou a solicitação")
    sdd_ack_p.add_argument("--result-ref", required=True, help="Arquivo relativo material produzido pelo consumidor")
    # CORR-2 (P1#4): ativação/desativação formal e durável da política SDD.
    sdd_sub.add_parser(
        "activate",
        help="Registra a ativação durável da política SDD do projeto (sdd-activation.yaml)",
    )
    sdd_sub.add_parser(
        "deactivate",
        help="Arquiva o registro durável de ativação SDD (desativação formal, com eco)",
    )
    sdd_sub.add_parser(
        "pilot-revoke",
        help="Indisponível no T0: exige futuro controle autenticado de revogação",
    )

    return parser


def _execute_command(squad: AgentSquad, args: argparse.Namespace) -> int:
    """Executa o comando já validado pelo parser e retorna seu código de saída."""
    if args.command == "migrate-handoffs":
        print(json.dumps(squad.migrate_handoffs(args.work_item, dry_run=args.dry_run), ensure_ascii=False, indent=2))
    elif args.command == "reclassify-work-item":
        result = squad.reclassify_work_item(
            args.work_item,
            from_type=args.from_type,
            to_type=args.to_type,
            dry_run=not args.apply,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.command == "init-work-item":
        kwargs: dict[str, Any] = {}
        if getattr(args, "item_type", None) is not None:
            kwargs["item_type"] = args.item_type
        if getattr(args, "devops", False):
            kwargs["devops"] = True
        if getattr(args, "story_points", None) is not None:
            kwargs["story_points"] = args.story_points
        if getattr(args, "parent_id", None) is not None:
            kwargs["parent_id"] = args.parent_id
        if getattr(args, "force", False):
            kwargs["force"] = True
        print(squad.init_work_item(args.id, args.risk, **kwargs))
    elif args.command == "light-start":
        print(squad.init_light_item(args.id, args.risk, args.objective))
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
            getattr(args, "author", None),
            getattr(args, "reviewer", None),
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
        if getattr(args, "deep", False):
            report = squad.deep_audit()
            print(json.dumps(report, ensure_ascii=False, indent=2))
            if not errors and not report["non_conformant_count"] and not report["orphans"]:
                print("AUDIT_OK")
                return 0
            if errors:
                print("\n".join(errors))
            return 1
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
    elif args.command == "init-project":
        result = squad.init_project(
            args.project_name,
            args.project_root,
            devops=args.devops,
            dry_run=args.dry_run,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.command == "advance-state":
        res = squad.advance_state(
            args.work_item,
            repair_entry=getattr(args, "repair_entry", False),
            actor=getattr(args, "actor", None),
            reason=getattr(args, "reason", None),
            authorization_ref=getattr(args, "authorization_ref", None),
        )
        print(json.dumps(res, ensure_ascii=False, indent=2))
    elif args.command == "run-continuous":
        from continuous_trigger_engine import ContinuousTriggerEngine

        engine = ContinuousTriggerEngine(squad)
        if getattr(args, "reset_circuit_breaker", False):
            engine.reset_circuit_breaker(args.work_item)
        res = engine.run_continuous(
            args.work_item,
            max_steps=getattr(args, "max_steps", 10),
            dry_run=getattr(args, "dry_run", False),
        )
        print(json.dumps(res, ensure_ascii=False, indent=2))
        if res.get("status") in {"HALTED_CIRCUIT_BREAKER", "ERROR"}:
            return 1
    elif args.command == "check-sizing":
        res = squad.check_sizing(points=args.points, item=args.work_item)
        print(json.dumps(res, ensure_ascii=False, indent=2))
    elif args.command == "index-codebase":
        res = squad.index_codebase(target_dir=args.target_dir)
        print(json.dumps(res, ensure_ascii=False, indent=2))
    elif args.command == "query-memory":
        res = squad.query_memory(item=args.work_item, kind=args.kind)
        print(json.dumps(res, ensure_ascii=False, indent=2))
    elif args.command == "memory-delta":
        fact_id = squad.memory_delta(args.work_item, args.author, args.statement, source=args.source, kind=args.kind)
        print(json.dumps({"fact_id": fact_id, "status": "recorded"}, ensure_ascii=False, indent=2))
    elif args.command == "sdd":
        if args.sdd_command == "init":
            result = squad.sdd_init(
                args.work_item, constitution_path=args.constitution, force=args.force
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.sdd_command == "status":
            print(json.dumps(squad.sdd_status(args.work_item), ensure_ascii=False, indent=2))
        elif args.sdd_command == "render":
            print(squad.sdd_render(args.work_item, args.stage))
        elif args.sdd_command == "run":
            print(json.dumps(squad.sdd_run(args.work_item, args.stage, dispatch=args.dispatch), ensure_ascii=False, indent=2))
        elif args.sdd_command == "stage-complete":
            print(json.dumps(squad.sdd_stage_complete(args.work_item, args.stage, output_refs=args.output_refs), ensure_ascii=False, indent=2))
        elif args.sdd_command == "dispatch-claim":
            print(json.dumps(squad.sdd_dispatch_claim(args.work_item, args.stage, args.consumer), ensure_ascii=False, indent=2))
        elif args.sdd_command == "dispatch-ack":
            print(json.dumps(squad.sdd_dispatch_ack(args.work_item, args.stage, args.consumer, args.result_ref), ensure_ascii=False, indent=2))
        elif args.sdd_command == "activate":
            print(json.dumps(squad.sdd_activate(), ensure_ascii=False, indent=2))
        elif args.sdd_command == "deactivate":
            print(json.dumps(squad.sdd_deactivate(), ensure_ascii=False, indent=2))
        elif args.sdd_command == "pilot-revoke":
            raise SquadError("SDD_PILOT_REVOKE_UNAVAILABLE")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Ponto de entrada CLI para comandos de controle e governança do squad."""
    signal.signal(signal.SIGINT, _signal_handler)
    args = _build_parser().parse_args(argv)
    root = args.root.resolve()
    project_name = args.project_name
    marker_root = args.project_root or find_project_root(Path.cwd())
    project_root = args.project_root
    if marker_root is not None and args.project_name is None:
        try:
            context = resolve_project_context(Path.cwd(), args.project_root)
        except ProjectContextError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        root = context.runtime_root
        project_name = context.project_id
        project_root = getattr(context, "project_root", None)
    squad = AgentSquad(root, project_name=project_name, project_root=project_root)
    try:
        return _execute_command(squad, args)
    except SquadError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
