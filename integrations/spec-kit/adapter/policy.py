"""Autorização de subetapas SDD por gate, identidade e hashes (T4) — backend-engineer.

Implementa o contrato ``authorize`` da seção 6 do plano:

    authorize(package: dict, stage: str, decisions: list[dict], policy: dict) -> list[dict]

Lista vazia significa "autorizado" (ausência de erros); não substitui
revisão semântica humana/especializada. Todos os caminhos falham
fechados: política inválida/ilegível, decisão sem vínculos verificáveis,
hash divergente, evidência ausente ou identidade não autorizada bloqueiam
a subetapa — nunca autorizam por omissão.

Mapeamentos (fontes reais, não inventadas):

- Estágio -> gates (briefing T4 / plano seção 5):
  ``planning`` -> G1-product; ``tasking`` -> G2-design;
  ``readiness`` -> G3-readiness; ``implementation`` -> G1+G2+G3.
- Gate -> owner autorizado (``config/workflow.yaml``, seção gates):
  G1-product -> product-owner; G2-design -> solution-architect;
  G3-readiness -> delivery-orchestrator; G4-code-security -> code-reviewer;
  G5-quality -> qa-engineer; G6-governance-release -> governance-auditor.
- Documento -> gate (invalidação por mudança de input, plano seção 6):
  spec/clarifications -> G1; plan -> G2; tasks -> G3; constituição -> todos.
- Cadeia de dependência entre gates: G1 -> G2 -> G3 (invalidação transitiva).

Estrutura da decisão: os registros replicam o YAML real produzido por
``scripts/agent_squad.py decide-gate`` (decision_id, gate_id,
work_item_id, decision, decider, criteria, evidence, human_approval,
conditions, valid_until, decided_at) com dois campos de vinculação
aditivos, obrigatórios quando a política SDD está ativada:

- ``input_hashes``: mapa ``documento -> sha256`` capturado no momento da
  decisão. A verificação usa EXCLUSIVAMENTE ``documents.*.sha256``
  (hash real recomputado do conteúdo por ``load_package`` — NOTA-1 do
  revisor); os hashes declarados em ``inputs.*.sha256`` NÃO são
  confiáveis e são apenas conferidos contra os hashes reais (divergência
  declarado-vs-real => pacote inconsistente => ``SDD_STALE_GATE``).
- ``policy_version``: versão da política vigente na decisão. Divergência
  da política atual => reavaliação obrigatória (``SDD_STALE_GATE``).

Política e modo legado:

- Política inválida (incluindo ``None``, não-dict ou schema violado)
  retorna ``SDD_POLICY_INVALID`` — falha fechada, nunca autoriza.
- ``is_sdd_required(policy)`` é o auxílio canônico para o chamador
  decidir se a garantia SDD está ativada (``sdd.required: true``).
- Política válida com ``sdd.required: false`` (projeto legado sem
  ativação) é compatível: ``authorize`` retorna lista vazia SEM executar
  as verificações de gate. IMPORTANTE: ausência de erros em modo legado
  NÃO constitui garantia/cobertura SDD — o chamador deve sinalizar a
  ausência consultando :func:`is_sdd_required` (plano seção 6).

Códigos de erro emitidos aqui (mesma forma de ``validate_package``:
``{code, path, message}``):

- ``SDD_MISSING_INPUT``  — gate obrigatório do estágio sem decisão
  aprovada, ou ``human_approval.required`` sem aprovação humana.
- ``SDD_STALE_GATE``     — hash de input divergente (real vs. vinculado
  na decisão, ou declarado vs. real), decisão sem ``input_hashes``/
  ``policy_version`` vinculados, ou invalidação transitiva por gate
  upstream.
- ``SDD_POLICY_INVALID`` — política inválida (via ``validate_policy``).
- ``SDD_MALFORMED``      — registro de decisão inutilizável: estágio
  desconhecido, work_item_id de outro item, decider não é owner do gate,
  autor/revisor idênticos (sem segregação), evidência ausente/fora do
  work item, ``decided_at`` ilegível, entrada de decisão não-dict.

Verificações de tempo (``valid_until``, expiração) são responsabilidade
da camada de estado do CLI (T5); este módulo não consulta o relógio.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from .contracts import resolve_within
from .validation import validate_policy

__all__ = [
    "GATE_OWNERS",
    "STAGE_GATES",
    "GATE_CHAIN",
    "GATE_INPUTS",
    "authorize",
    "is_sdd_required",
]

# Gate -> owner autorizado (config/workflow.yaml, seção gates).
GATE_OWNERS: dict[str, str] = {
    "G1-product": "product-owner",
    "G2-design": "solution-architect",
    "G3-readiness": "delivery-orchestrator",
    "G4-code-security": "code-reviewer",
    "G5-quality": "qa-engineer",
    "G6-governance-release": "governance-auditor",
}

# Estágio -> gates cujas decisões aprovadas são obrigatórias.
STAGE_GATES: dict[str, tuple[str, ...]] = {
    "planning": ("G1-product",),
    "tasking": ("G2-design",),
    "readiness": ("G3-readiness",),
    "implementation": ("G1-product", "G2-design", "G3-readiness"),
}

# Cadeia de dependência entre gates (invalidação transitiva: upstream
# stale invalida todos os downstream).
GATE_CHAIN: tuple[str, ...] = ("G1-product", "G2-design", "G3-readiness")

# Inputs vinculados por decisão de gate. A constituição é vinculada por
# todos os gates: mudança de constituição exige reavaliação de todos os
# gates afetados (plano seção 6).
GATE_INPUTS: dict[str, tuple[str, ...]] = {
    "G1-product": ("spec", "clarifications", "constitution"),
    "G2-design": ("plan", "constitution"),
    "G3-readiness": ("tasks", "constitution"),
}

# Documento -> gates invalidados quando o documento muda.
_DOC_TO_GATES: dict[str, tuple[str, ...]] = {
    "spec": ("G1-product",),
    "clarifications": ("G1-product",),
    "plan": ("G2-design",),
    "tasks": ("G3-readiness",),
    "constitution": ("G1-product", "G2-design", "G3-readiness"),
}


def _err(code: str, path: str, message: str) -> dict:
    return {"code": code, "path": path, "message": message}


def is_sdd_required(policy: object) -> bool:
    """Retorna ``True`` somente quando a política ativa exige SDD.

    Projeto legado sem ativação (política ausente, ``sdd.required``
    ausente/false, ou política não-dict) retorna ``False``: compatível,
    mas SEM garantia SDD — o chamador não pode divulgar cobertura SDD.
    """
    if not isinstance(policy, dict):
        return False
    sdd = policy.get("sdd")
    return isinstance(sdd, dict) and sdd.get("required") is True


def _parse_decided_at(value: object) -> datetime | None:
    """Converte ``decided_at`` ISO-8601 para datetime aware em UTC.

    MAJOR-2 (security-reviewer): registros ISO-8601 válidos podem vir com
    fuso (``...Z``/offset) ou sem (naive). Comparar naive com aware levanta
    ``TypeError`` e derrubaria o chamador — o contrato
    ``authorize -> list[dict]`` nunca propaga exceção de registro
    malformado. Escolha documentada: ``decided_at`` sem fuso é interpretado
    como UTC (não é rejeitado; os dois formatos são ISO-8601 válidos e a
    ordenação fica determinística). Falha de parse continua virando
    ``SDD_MALFORMED`` via chamador.
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _latest_decision(decisions: list, gate: str, errors: list[dict]) -> dict | None:
    """Seleciona a decisão mais recente para o gate; registros inutilizáveis viram erro."""
    candidates = []
    for index, record in enumerate(decisions):
        if not isinstance(record, dict):
            errors.append(
                _err("SDD_MALFORMED", f"decisions[{index}]", "registro de decisão deve ser um objeto (dict)")
            )
            continue
        if record.get("gate_id") != gate:
            continue
        parsed = _parse_decided_at(record.get("decided_at"))
        if parsed is None:
            errors.append(
                _err(
                    "SDD_MALFORMED",
                    f"decisions[{index}].decided_at",
                    "decided_at ausente ou ilegível (ISO-8601 esperado)",
                )
            )
            continue
        candidates.append((parsed, index, record))
    if not candidates and not errors:
        return None
    if not candidates:
        return None
    return max(candidates, key=lambda item: (item[0], item[1]))[2]


def _check_evidence(record: dict, work_dir: object, errors: list[dict]) -> bool:
    """Evidência deve existir como arquivo dentro do work item. Retorna ok."""
    gate = record.get("gate_id")
    evidence = record.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        errors.append(
            _err(
                "SDD_MALFORMED",
                f"decisions.{gate}.evidence",
                "decisão sem evidência: lista vazia ou ausente",
            )
        )
        return False
    if not isinstance(work_dir, str) or not work_dir:
        errors.append(
            _err(
                "SDD_MALFORMED",
                f"decisions.{gate}.evidence",
                "package sem work_dir: evidência não é verificável (falha fechada)",
            )
        )
        return False
    base = Path(work_dir)
    ok = True
    for item in evidence:
        if not isinstance(item, str) or not item:
            errors.append(
                _err("SDD_MALFORMED", f"decisions.{gate}.evidence", "entrada de evidência não é um caminho válido")
            )
            ok = False
            continue
        resolved = resolve_within(base, item)
        if resolved is None or not resolved.is_file():
            errors.append(
                _err(
                    "SDD_MALFORMED",
                    f"decisions.{gate}.evidence",
                    f"evidência ausente ou fora do work item: {item}",
                )
            )
            ok = False
    return ok


def _check_identity(record: dict, errors: list[dict]) -> bool:
    """Decider deve ser owner do gate; autor != revisor (segregação de papéis)."""
    gate = record.get("gate_id")
    owner = GATE_OWNERS.get(gate)
    decider = record.get("decider")
    reviewer = record.get("reviewer")
    author = record.get("author")
    ok = True
    if owner is not None and decider != owner:
        errors.append(
            _err(
                "SDD_MALFORMED",
                f"decisions.{gate}.decider",
                f"decider {decider!r} não é o owner autorizado do gate {gate} (esperado: {owner!r})",
            )
        )
        ok = False
    if reviewer is not None:
        if not isinstance(reviewer, str) or not reviewer:
            errors.append(
                _err("SDD_MALFORMED", f"decisions.{gate}.reviewer", "identidade de revisor vazia ou inválida")
            )
            ok = False
        elif reviewer in {decider, author}:
            errors.append(
                _err(
                    "SDD_MALFORMED",
                    f"decisions.{gate}.reviewer",
                    "autor/revisor ou decider/revisor idênticos: segregação de papéis violada",
                )
            )
            ok = False
    return ok


def _check_human_approval(record: dict, errors: list[dict]) -> bool:
    """``human_approval.required`` exige aprovação humana real com evidência."""
    gate = record.get("gate_id")
    approval = record.get("human_approval")
    if approval is None:
        return True  # campo opcional nos registros legados; ausente = não exigido
    if not isinstance(approval, dict):
        errors.append(
            _err("SDD_MALFORMED", f"decisions.{gate}.human_approval", "human_approval deve ser um objeto")
        )
        return False
    if approval.get("required") is not True:
        return True
    problems: list[str] = []
    if approval.get("status") != "approved":
        problems.append("status != 'approved'")
    if not isinstance(approval.get("approved_by"), str) or not approval.get("approved_by"):
        problems.append("approved_by ausente")
    evidence = approval.get("evidence")
    if evidence is not None and not isinstance(evidence, str):
        problems.append("evidence inválido")
    if problems:
        errors.append(
            _err(
                "SDD_MISSING_INPUT",
                f"decisions.{gate}.human_approval",
                "human_approval: aprovação humana exigida e não concedida: " + "; ".join(problems),
            )
        )
        return False
    return True


def _stale_from_package(package: dict, documents: dict, inputs: dict) -> tuple[set[str], list[str]]:
    """Gates stale e documentos divergentes entre declaração (package.json) e hash real.

    Os hashes declarados em ``inputs.*.sha256`` e ``constitution_sha256``
    não são confiáveis (NOTA-1); quando divergem do hash real do conteúdo
    (``documents.*.sha256``), o pacote está inconsistente e os gates que
    dependem do documento são stale. Hash real ``None`` (documento ausente,
    ilegível ou não carregado) é stale por si só — MAJOR-1
    (security-reviewer): nenhum gate autoriza sobre documento inexistente,
    mesmo que o declarado também seja ``None``.

    Retorna ``(gates_stale, documentos_divergentes)`` para mensagem auditável.
    """
    stale: set[str] = set()
    divergent: list[str] = []

    def _mark(label: str) -> None:
        divergent.append(label)
        stale.update(_DOC_TO_GATES.get(label, ()))

    for key in ("spec", "clarifications", "plan", "tasks"):
        entry = inputs.get(key) if isinstance(inputs, dict) else None
        document = documents.get(key)
        real = document.get("sha256") if isinstance(document, dict) else None
        declared = entry.get("sha256") if isinstance(entry, dict) else None
        if real is None or declared != real:
            _mark(key)
    constitution = documents.get("constitution")
    real_const = constitution.get("sha256") if isinstance(constitution, dict) else None
    if real_const is None or package.get("constitution_sha256") != real_const:
        _mark("constitution")
    return stale, divergent


def authorize(package: dict, stage: str, decisions: list[dict], policy: dict) -> list[dict]:
    """Autoriza a subetapa ``stage`` do pacote sob a política e decisões dadas.

    Consulte o docstring do módulo para o contrato completo. Falha fechada:
    qualquer dúvida resulta em erro, nunca em autorização.
    """
    # 1. Política: inválida/ilegível falha fechada (SDD_POLICY_INVALID).
    policy_errors = validate_policy(policy)
    if policy_errors:
        return policy_errors
    if not is_sdd_required(policy):
        # Modo legado (projeto sem ativação SDD): compatível, sem garantia.
        return []

    # 2. Estágio conhecido?
    gates = STAGE_GATES.get(stage)
    if gates is None:
        return [_err("SDD_MALFORMED", "stage", f"estágio desconhecido: {stage!r}")]
    required_set = set(gates)
    # Gates fora da cadeia (G4/G5/G6) não são autorizados por subetapa;
    # cada um é avaliado individualmente se algum dia for exigido.
    off_chain = [gate for gate in gates if gate not in GATE_CHAIN]
    # A cadeia é varrida desde G1: um gate só é avaliado se for exigido
    # pelo estágio ou for antecessor (pré-requisito transitivo) de um
    # gate exigido — a invalidação transitiva parte do upstream.
    chain_index = {gate: index for index, gate in enumerate(GATE_CHAIN)}
    deepest = max(chain_index[gate] for gate in gates if gate in chain_index)
    relevant = set(GATE_CHAIN[: deepest + 1])

    if not isinstance(package, dict):
        return [_err("SDD_MALFORMED", "package", "pacote deve ser um objeto (dict)")]
    if not isinstance(decisions, list):
        return [_err("SDD_MALFORMED", "decisions", "decisões devem ser uma lista")]
    work_id = package.get("work_id")
    documents = package.get("documents") if isinstance(package.get("documents"), dict) else {}
    inputs = package.get("inputs") if isinstance(package.get("inputs"), dict) else {}
    work_dir = package.get("work_dir")

    errors: list[dict] = []
    stale_by_declaration, divergent_documents = _stale_from_package(package, documents, inputs)

    # Identidade do pacote: revisões com autor == revisor são inválidas.
    reviews = package.get("reviews")
    if isinstance(reviews, list):
        for index, review in enumerate(reviews):
            if isinstance(review, dict) and review.get("author") and review.get("author") == review.get("reviewer"):
                errors.append(
                    _err(
                        "SDD_MALFORMED",
                        f"reviews[{index}]",
                        "autor igual a revisor no pacote: segregação de papéis violada",
                    )
                )

    policy_version = policy.get("policy_version")
    propagated_stale = False

    for gate in (*GATE_CHAIN, *off_chain):
        if gate not in relevant and gate not in off_chain:
            continue
        path = f"decisions.{gate}"
        is_required = gate in required_set
        if propagated_stale:
            errors.append(
                _err(
                    "SDD_STALE_GATE",
                    path,
                    f"gate {gate} invalidado transitivamente por gate upstream stale",
                )
            )
            continue
        if gate in stale_by_declaration:
            errors.append(
                _err(
                    "SDD_STALE_GATE",
                    path,
                    f"gate {gate} stale: hash declarado no package.json diverge do hash real "
                    f"(ou hash real ausente) dos documentos: {', '.join(divergent_documents)}",
                )
            )
            propagated_stale = True
            continue

        record = _latest_decision(decisions, gate, errors)
        if record is None:
            role = (
                f"gate {gate} obrigatório para o estágio {stage!r}"
                if is_required
                else f"gate {gate}, pré-requisito transitivo do estágio {stage!r}"
            )
            errors.append(
                _err("SDD_MISSING_INPUT", path, f"{role} sem decisão aprovada vinculada")
            )
            propagated_stale = True
            continue

        # work_id: decisão de outro work item nunca autoriza este pacote.
        if record.get("work_item_id") != work_id:
            errors.append(
                _err(
                    "SDD_MALFORMED",
                    f"{path}.work_item_id",
                    f"decisão pertence a outro work item: {record.get('work_item_id')!r} != {work_id!r}",
                )
            )
            propagated_stale = True
            continue

        if record.get("decision") != "approved":
            errors.append(
                _err(
                    "SDD_MISSING_INPUT",
                    path,
                    f"decisão do gate {gate} não está aprovada (decision={record.get('decision')!r})",
                )
            )
            propagated_stale = True
            continue

        identity_ok = _check_identity(record, errors)
        approval_ok = _check_human_approval(record, errors)
        evidence_ok = _check_evidence(record, work_dir, errors)

        # Vinculação de hashes: obrigatória quando a política SDD está ativada.
        input_hashes = record.get("input_hashes")
        if not isinstance(input_hashes, dict) or not input_hashes:
            errors.append(
                _err(
                    "SDD_STALE_GATE",
                    f"{path}.input_hashes",
                    f"decisão do gate {gate} sem hashes de input vinculados: frescor não é verificável",
                )
            )
            propagated_stale = True
        else:
            if record.get("policy_version") != policy_version:
                errors.append(
                    _err(
                        "SDD_STALE_GATE",
                        f"{path}.policy_version",
                        f"policy_version da decisão ({record.get('policy_version')!r}) diverge da política "
                        f"vigente ({policy_version!r}): reavaliação obrigatória",
                    )
                )
                propagated_stale = True
            for doc in GATE_INPUTS.get(gate, ()):
                document = documents.get(doc)
                real = document.get("sha256") if isinstance(document, dict) else None
                bound = input_hashes.get(doc)
                # MAJOR-1 (security-reviewer): hash real None => stale, em AMBOS
                # os pontos de verificação; `bound != real` sozinho deixaria
                # passar vínculo None == None (documento inexistente).
                if real is None:
                    errors.append(
                        _err(
                            "SDD_STALE_GATE",
                            f"{path}.input_hashes.{doc}",
                            f"documento '{doc}' ausente ou sem hash real: nenhum gate "
                            "pode autorizar sobre documento inexistente",
                        )
                    )
                    propagated_stale = True
                elif bound != real:
                    errors.append(
                        _err(
                            "SDD_STALE_GATE",
                            f"{path}.input_hashes.{doc}",
                            f"hash do documento '{doc}' diverge do vinculado na decisão do gate {gate}: "
                            "input alterado após a aprovação",
                        )
                    )
                    propagated_stale = True

        if not (identity_ok and approval_ok and evidence_ok):
            propagated_stale = True

    return errors
