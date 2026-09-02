"""Handoff Toolkit — template e validador de handoff entre runtimes/agentes.

Gera handoffs validados contra ``contracts/handoff.schema.json`` e valida
handoffs existentes. Consumível por qualquer runtime via CLI ou API.

Uso:
    from scripts.handoff_toolkit import create_handoff, validate_handoff, acknowledge_handoff

    h = create_handoff(
        work_item_id="EPIC-001",
        from_agent="software-engineer",
        to_agent="code-reviewer",
        summary="Implementação concluída",
        artifacts=["code/src/foo.py", "tests/test_foo.py"],
        evidence=["pytest -q", "python scripts/verify_clean_code.py"],
    )
    h.to_yaml(Path("work/EPIC-001/handoffs/HANDOFF-001.yaml"))

    result = validate_handoff(Path("work/EPIC-001/handoffs/HANDOFF-001.yaml"))

    ack = acknowledge_handoff(
        handoff_path=Path("work/EPIC-001/handoffs/HANDOFF-001.yaml"),
        acknowledged_by="code-reviewer",
        status="accepted",
    )
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Modelos
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Handoff:
    id: str
    work_item_id: str
    from_agent: str
    to_agent: str
    created_at: str
    status: str = "ready"
    summary: str = ""
    artifacts: list[str] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    memory_delta: str = ""
    next_gate: Optional[str] = None
    acceptance_criteria_checked: list[str] = field(default_factory=list)
    acknowledgement_status: str = "pending"
    acknowledged_by: str = ""
    acknowledged_at: Optional[str] = None
    acknowledgement_note: str = ""


# ---------------------------------------------------------------------------
# Handoff helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _work_item_dir(work_item_id: str, root: Path) -> Path:
    return root / "work" / work_item_id


def _next_handoff_id(work_item_dir: Path) -> str:
    handoffs_dir = work_item_dir / "handoffs"
    existing = list(handoffs_dir.glob("HANDOFF-*.yaml")) if handoffs_dir.exists() else []
    seq = len(existing) + 1
    return f"HANDOFF-{work_item_dir.name}-{seq:03d}"


def create_handoff(
    work_item_id: str,
    from_agent: str,
    to_agent: str,
    summary: str,
    artifacts: list[str],
    evidence: list[str],
    decisions: list[str] | None = None,
    open_questions: list[str] | None = None,
    risks: list[str] | None = None,
    memory_delta: str = "",
    next_gate: str | None = None,
    acceptance_criteria_checked: list[str] | None = None,
    root: Path | None = None,
) -> Handoff:
    """Cria um handoff validado contra o schema.

    Args:
        work_item_id: ID do work item (ex: ``EPIC-001``).
        from_agent: agente remetente.
        to_agent: agente destinatário.
        summary: resumo da entrega.
        artifacts: lista de caminhos de artefatos produzidos.
        evidence: lista de evidências executadas (comandos, logs, diffs).
        decisions: decisões tomadas durante o trabalho.
        open_questions: perguntas em aberto para o destinatário.
        risks: riscos identificados.
        memory_delta: caminho para o delta de memória (ex: ``memory/deltas/MEM-EPIC-001-001.yaml``).
        next_gate: próximo gate a ser executado (ex: ``G1-product``).
        acceptance_criteria_checked: critérios de aceite verificados.
        root: raiz do squad. Se ``None``, detecta automaticamente.

    Returns:
        ``Handoff`` pronto para ser escrito em YAML.
    """
    root = root or Path(__file__).resolve().parent.parent
    work_item_dir = _work_item_dir(work_item_id, root)
    handoff_id = _next_handoff_id(work_item_dir)

    return Handoff(
        id=handoff_id,
        work_item_id=work_item_id,
        from_agent=from_agent,
        to_agent=to_agent,
        created_at=_now_iso(),
        status="ready",
        summary=summary,
        artifacts=artifacts or [],
        decisions=decisions or [],
        open_questions=open_questions or [],
        risks=risks or [],
        evidence=evidence or [],
        memory_delta=memory_delta,
        next_gate=next_gate,
        acceptance_criteria_checked=acceptance_criteria_checked or [],
        acknowledgement_status="pending",
        acknowledged_by="",
        acknowledged_at=None,
        acknowledgement_note="",
    )


def validate_handoff(handoff_path: Path) -> dict[str, Any]:
    """Valida um handoff YAML contra o schema e regras de negócio.

    Returns:
        Dict com ``valid``, ``errors`` e ``warnings``.
    """
    schema_path = handoff_path.parent.parent.parent / "contracts" / "handoff.schema.json"
    if not schema_path.exists():
        schema_path = Path(__file__).resolve().parent.parent / "contracts" / "handoff.schema.json"

    errors: list[str] = []
    warnings: list[str] = []

    try:
        import yaml
        data = yaml.safe_load(handoff_path.read_text(encoding="utf-8")) or {}
    except Exception as e:
        return {"valid": False, "errors": [f"YAML parse error: {e}"], "warnings": []}

    if not isinstance(data, dict):
        return {"valid": False, "errors": ["handoff root is not a mapping"], "warnings": []}

    required = ["id", "work_item_id", "from", "to", "created_at", "status", "summary", "artifacts", "evidence", "memory_delta", "next_gate", "acceptance", "acknowledgement"]
    for field_name in required:
        if field_name not in data:
            errors.append(f"missing required field: {field_name}")

    if "acknowledgement" in data and isinstance(data["acknowledgement"], dict):
        ack = data["acknowledgement"]
        if ack.get("status") != "pending" and not ack.get("acknowledged_by"):
            warnings.append("acknowledged_by is empty but status is not pending")

    if data.get("status") == "ready" and not data.get("evidence"):
        errors.append("status=ready requires at least one evidence item")

    return {"valid": len(errors) == 0, "errors": errors, "warnings": warnings}


def acknowledge_handoff(
    handoff_path: Path,
    acknowledged_by: str,
    status: str = "accepted",
    note: str = "",
    root: Path | None = None,
) -> dict[str, Any]:
    """Aceita/rejeita um handoff, atualizando o arquivo YAML.

    Args:
        handoff_path: caminho do arquivo HANDOFF-*.yaml.
        acknowledged_by: ID do agente que está ACK-ing.
        status: ``accepted`` ou ``rejected``.
        note: nota opcional do destinatário.

    Returns:
        Dict com ``updated``, ``path`` e ``status``.
    """
    import yaml

    if status not in ("accepted", "rejected"):
        raise ValueError(f"invalid acknowledgement status: {status}")

    data = yaml.safe_load(handoff_path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError("handoff file is not a valid YAML mapping")

    data["acknowledgement"] = {
        "status": status,
        "acknowledged_by": acknowledged_by,
        "acknowledged_at": _now_iso(),
        "note": note,
    }

    handoff_path.write_text(
        yaml.dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )

    return {"updated": True, "path": str(handoff_path), "status": status}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    """Executa a interface de linha de comando do toolkit de handoffs."""
    import argparse
    parser = argparse.ArgumentParser(description="Handoff toolkit para agentes do squad.")
    sub = parser.add_subparsers(dest="command")

    validate_p = sub.add_parser("validate", help="Valida um handoff YAML")
    validate_p.add_argument("path", type=Path, help="Caminho do HANDOFF-*.yaml")

    ack_p = sub.add_parser("ack", help="Acknowledges/rejeita um handoff")
    ack_p.add_argument("path", type=Path, help="Caminho do HANDOFF-*.yaml")
    ack_p.add_argument("--by", required=True, help="Agente que está ACK-ing")
    ack_p.add_argument("--status", default="accepted", choices=["accepted", "rejected"])
    ack_p.add_argument("--note", default="")

    args = parser.parse_args(argv or [])

    if args.command == "validate":
        result = validate_handoff(args.path)
        print(f"VALID: {result['valid']}")
        if result["errors"]:
            print("ERRORS:")
            for e in result["errors"]:
                print(f"  - {e}")
        if result["warnings"]:
            print("WARNINGS:")
            for w in result["warnings"]:
                print(f"  - {w}")
        return 0 if result["valid"] else 1

    if args.command == "ack":
        result = acknowledge_handoff(args.path, args.by, args.status, args.note)
        print(f"ACK_OK status={result['status']} path={result['path']}")
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
