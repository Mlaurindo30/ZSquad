"""Auto-correção inspirada no Prime Agent (planRefinement + apply + rollback).

NÃO é um runtime próprio: apenas a lógica de refinement plugável em
qualquer LLM provider (Codex, Copilot, Claude, etc.).

Uso:
    from scripts.auto_correction import (
        RefinementProposal,
        RefinementResult,
        plan_refinement,
        apply_refinement_proposal,
        rollback,
        summarize_for_refinement,
    )

    proposal = plan_refinement(
        messages=conversation,
        state=current_state,
        history=refinement_history,
        llm_call=my_llm_callable,
        scope="local",
    )

    result = apply_refinement_proposal(proposal, state)
    # Se algo quebrar:
    rolled = rollback(result, state)
"""

from __future__ import annotations

import copy
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Modelos
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RefinementEdit:
    action: str  # "create" | "update" | "delete"
    kind: str  # "file" | "config" | "script" | "skill" | "decision"
    id: Optional[str] = None
    title: Optional[str] = None
    content: Optional[str] = None
    path: Optional[str] = None
    reference: Optional[Dict[str, Any]] = None
    arguments: Optional[Dict[str, Any]] = None
    metadata: Optional[Dict[str, Any]] = None
    reason: Optional[str] = None


@dataclass(frozen=True)
class RefinementProposal:
    summary: str
    rationale: str
    edits: List[RefinementEdit]
    expected_outcome: str


@dataclass(frozen=True)
class AppliedRefinementEdit(RefinementEdit):
    edit_id: str = ""
    before: Optional[Dict[str, Any]] = None
    after: Optional[Dict[str, Any]] = None
    applied: bool = False
    error: Optional[str] = None


@dataclass(frozen=True)
class RefinementEvent:
    id: str
    trigger: str
    changes: List[str]
    evidence: str
    outcome: str
    created_at: str


@dataclass(frozen=True)
class RefinementResult:
    id: str
    summary: str
    rationale: str
    expected_outcome: str
    applied_edits: List[AppliedRefinementEdit]
    rollback_of: Optional[str] = None
    scope: str = "local"
    created_at: str = field(default_factory=lambda: _now())


@dataclass
class RefinementHistoryEntry:
    id: str
    summary: str
    scope: str
    applied_edits: List[AppliedRefinementEdit]
    created_at: str
    rolled_back: bool = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slug(raw: str, fallback: str) -> str:
    normalized = "".join(ch.lower() if ch.isalnum() else "_" for ch in raw.strip())
    normalized = "_".join(part for part in normalized.split("_") if part)
    return (normalized or fallback)[:80]


def _truncate(text: str, max_chars: int = 80_000) -> str:
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


# ---------------------------------------------------------------------------
# Estado do harness (adaptado para agent_squad)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HarnessState:
    """Estado mutável do agente/squad.

    Em vez do modelo completo do Prime Agent, usamos um estado genérico
    baseado em dicionários para funcionar com qualquer domínio.
    """

    entries: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    refinements: List[RefinementEvent] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def clone(self) -> HarnessState:
        """Cria uma cópia independente de um valor YAML serializável."""
        return HarnessState(
            entries=copy.deepcopy(self.entries),
            refinements=list(self.refinements),
            metadata=copy.deepcopy(self.metadata),
        )


# ---------------------------------------------------------------------------
# LLM callable
# ---------------------------------------------------------------------------


LLMCallable = Callable[[str, str, Optional[str]], str]
"""Assinatura: llm_call(system_prompt: str, user_prompt: str, signal: str | None) -> str

Deve retornar o texto final da resposta do modelo. Em caso de erro,
deve levantar exceção.
"""


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------


def _serialize_messages(messages: List[Dict[str, Any]], max_chars: int = 80_000) -> str:
    lines: List[str] = []
    for msg in messages:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        if isinstance(content, list):
            parts: List[str] = []
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        parts.append(block.get("text", ""))
                    else:
                        parts.append(json.dumps(block, ensure_ascii=False))
                else:
                    parts.append(str(block))
            content = "\n".join(parts)
        lines.append(f"[{role}]\n{content}\n")
    serialized = "\n".join(lines)
    return _truncate(serialized, max_chars)


def _serialize_state(state: HarnessState) -> str:
    entries_summary: List[str] = []
    for kind, records in state.entries.items():
        entries_summary.append(f"[{kind}]")
        for entry_id, entry in records.items():
            if isinstance(entry, dict):
                title = entry.get("title", entry_id)
                entries_summary.append(f"  {entry_id}: {title}")
            else:
                entries_summary.append(f"  {entry_id}")
    refinements_summary = []
    for ref in state.refinements[-10:]:
        refinements_summary.append(f"- {ref.id}: {ref.trigger}")
    return "\n".join([
        "<harness_entries>\n" + "\n".join(entries_summary) + "\n</harness_entries>",
        "<refinement_history>\n" + "\n".join(refinements_summary) + "\n</refinement_history>",
    ])


def _serialize_history(history: List[RefinementHistoryEntry]) -> str:
    if not history:
        return "<refinement_history>\n(none)\n</refinement_history>"
    lines = []
    for entry in history[-10:]:
        lines.append(f"- {entry.id}: {entry.summary} (scope={entry.scope}, rolled_back={entry.rolled_back})")
    return "<refinement_history>\n" + "\n".join(lines) + "\n</refinement_history>"


def build_refinement_prompt(
    messages: List[Dict[str, Any]],
    state: HarnessState,
    history: List[RefinementHistoryEntry],
    scope: str = "local",
    instructions: Optional[str] = None,
) -> str:
    """Monta o prompt de refinement para o LLM.

    O prompt pede JSON puro com edits. Se nenhuma edição for justificada,
    retorna array vazio com rationale.
    """
    scope_instruction = (
        "Requested refinement scope: global. "
        "Only propose stable, cross-session edits: durable user preferences, "
        "reusable skills, explicitly project-qualified facts. "
        "Do NOT persist session-only progress, temporary blockers, or current-run coordination."
        if scope == "global"
        else "Requested refinement scope: local. "
        "Prefer local edits for current task progress, temporary blockers, "
        "current-run coordination, and project facts not clearly reusable across sessions. "
        "Global entries are read-only context: do not propose update or delete for them; "
        "create a local entry instead if an override is needed."
    )

    parts = [
        "<current_harness_state>",
        _serialize_state(state),
        "</current_harness_state>",
        "<refinement_history>",
        _serialize_history(history),
        "</refinement_history>",
        "<conversation>",
        _serialize_messages(messages),
        "</conversation>",
        "<scope_policy>",
        scope_instruction,
        "</scope_policy>",
    ]
    if instructions:
        parts.extend([
            "<user_refine_instructions>",
            instructions,
            "</user_refine_instructions>",
        ])
    parts.append(
        "Return ONLY a JSON object with this shape:\n"
        "{\n"
        '  "summary": "<short summary>",\n'
        '  "rationale": "<why these edits>",\n'
        '  "expected_outcome": "<what improves>",\n'
        '  "edits": [\n'
        "    {\n"
        '      "action": "create" | "update" | "delete",\n'
        '      "kind": "file" | "config" | "script" | "skill" | "decision",\n'
        '      "id": "<optional-id>",\n'
        '      "title": "<optional-title>",\n'
        '      "content": "<optional-content>",\n'
        '      "path": "<optional-path>",\n'
        '      "reason": "<optional-reason>"\n'
        "    }\n"
        "  ]\n"
        "}\n"
        "If no useful edit is justified, return {\"summary\": \"no-op\", \"rationale\": \"...\", "
        "\"expected_outcome\": \"no change\", \"edits\": []}."
    )
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------


def plan_refinement(
    messages: List[Dict[str, Any]],
    state: HarnessState,
    history: List[RefinementHistoryEntry],
    llm_call: LLMCallable,
    scope: str = "local",
    instructions: Optional[str] = None,
    signal: Optional[str] = None,
) -> RefinementPlan:
    """Produz uma proposta de refinement SEM mutar o estado.

    Args:
        messages: conversação completa (últimos 80k chars são usados).
        state: estado atual do harness (snapshot).
        history: histórico de refinements anteriores.
        llm_call: callable que envia prompt para o LLM e retorna texto final.
        scope: "local" (work item) ou "global" (cross-session).
        instructions: instruções adicionais do usuário.
        signal: string de sinalização para abort (opcional).

    Returns:
        RefinementPlan com proposal + id + baseline snapshot.

    Raises:
        Exception: se o LLM falhar ou retornar JSON inválido.
    """
    proposal_id = f"refine_{int(time.time() * 1000)}"
    user_prompt = build_refinement_prompt(messages, state, history, scope, instructions)

    try:
        text = llm_call(
            system_prompt=REFINEMENT_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            signal=signal,
        )
        proposal = _parse_proposal(text)
    except Exception as exc:
        raise RuntimeError(f"Refinement LLM call failed: {exc}") from exc

    baseline = state.clone()

    return RefinementPlan(
        proposal=proposal,
        id=proposal_id,
        rollback_of=None,
        rollback_scope=scope,
        baseline_state=baseline,
    )


def apply_refinement_proposal(
    proposal: RefinementPlan,
    state: HarnessState,
    history: List[RefinementHistoryEntry],
) -> RefinementResult:
    """Aplica proposta atomicamente, registra evento, retorna resultado.

    Valida cada edição contra baseline_state para detectar conflitos.
    """
    applied_edits: List[AppliedRefinementEdit] = []
    proposal_modified_keys: set[str] = set()

    for edit in proposal.proposal.edits:
        computed_id = edit.id or (_slug(edit.title or edit.kind, edit.kind) if edit.action == "create" else "")
        edit_id = computed_id or ""
        validation_error = _validate_edit(edit, edit_id)
        if validation_error:
            applied_edits.append(AppliedRefinementEdit(
                action=edit.action, kind=edit.kind, id=edit_id,
                title=edit.title, content=edit.content, path=edit.path,
                reference=edit.reference, arguments=edit.arguments,
                metadata=edit.metadata, reason=edit.reason,
                edit_id=edit_id, applied=False, error=validation_error,
            ))
            continue

        kind_records = state.entries.setdefault(edit.kind, {})
        before = copy.deepcopy(kind_records.get(edit_id))
        entry_key = f"{edit.kind}:{edit_id}"
        baseline_entry = proposal.baseline_state.entries.get(edit.kind, {}).get(edit_id) if proposal.baseline_state else None

        if proposal.baseline_state and entry_key not in proposal_modified_keys:
            if json.dumps(before, sort_keys=True, default=str) != json.dumps(baseline_entry, sort_keys=True, default=str):
                applied_edits.append(AppliedRefinementEdit(
                    action=edit.action, kind=edit.kind, id=edit_id,
                    title=edit.title, content=edit.content, path=edit.path,
                    reference=edit.reference, arguments=edit.arguments,
                    metadata=edit.metadata, reason=edit.reason,
                    edit_id=edit_id, before=before, applied=False,
                    error="entry changed during refinement planning",
                ))
                continue

        if edit.action == "delete":
            if before is None:
                applied_edits.append(AppliedRefinementEdit(
                    action=edit.action, kind=edit.kind, id=edit_id,
                    title=edit.title, content=edit.content, path=edit.path,
                    reference=edit.reference, arguments=edit.arguments,
                    metadata=edit.metadata, reason=edit.reason,
                    edit_id=edit_id, before=before, applied=False,
                    error="entry not found",
                ))
                continue
            del kind_records[edit_id]
            proposal_modified_keys.add(entry_key)
            applied_edits.append(AppliedRefinementEdit(
                action=edit.action, kind=edit.kind, id=edit_id,
                title=edit.title, content=edit.content, path=edit.path,
                reference=edit.reference, arguments=edit.arguments,
                metadata=edit.metadata, reason=edit.reason,
                edit_id=edit_id, before=before, after=None, applied=True,
            ))
            continue

        if edit.action == "create" and before is not None:
            applied_edits.append(AppliedRefinementEdit(
                action=edit.action, kind=edit.kind, id=edit_id,
                title=edit.title, content=edit.content, path=edit.path,
                reference=edit.reference, arguments=edit.arguments,
                metadata=edit.metadata, reason=edit.reason,
                edit_id=edit_id, before=before, applied=False,
                error="entry already exists",
            ))
            continue

        if edit.action == "update" and before is None:
            applied_edits.append(AppliedRefinementEdit(
                action=edit.action, kind=edit.kind, id=edit_id,
                title=edit.title, content=edit.content, path=edit.path,
                reference=edit.reference, arguments=edit.arguments,
                metadata=edit.metadata, reason=edit.reason,
                edit_id=edit_id, before=before, applied=False,
                error="entry not found",
            ))
            continue

        after_dict = {
            "title": edit.title,
            "content": edit.content,
            "path": edit.path,
            "reference": edit.reference,
            "arguments": edit.arguments,
            "metadata": edit.metadata,
        }
        kind_records[edit_id] = after_dict
        proposal_modified_keys.add(entry_key)
        applied_edits.append(AppliedRefinementEdit(
            action=edit.action, kind=edit.kind, id=edit_id,
            title=edit.title, content=edit.content, path=edit.path,
            reference=edit.reference, arguments=edit.arguments,
            metadata=edit.metadata, reason=edit.reason,
            edit_id=edit_id, before=before, after=after_dict, applied=True,
        ))

    now = _now()
    changes = [
        f"{e.action} {e.kind}:{e.edit_id}" for e in applied_edits if e.applied
    ]
    event = RefinementEvent(
        id=proposal.id,
        trigger=proposal.proposal.summary,
        changes=changes,
        evidence=proposal.proposal.rationale,
        outcome=proposal.proposal.expected_outcome,
        created_at=now,
    )
    state.refinements.append(event)
    history.append(RefinementHistoryEntry(
        id=proposal.id,
        summary=proposal.proposal.summary,
        scope=proposal.rollback_scope or "local",
        applied_edits=applied_edits,
        created_at=now,
        rolled_back=False,
    ))

    return RefinementResult(
        id=proposal.id,
        summary=proposal.proposal.summary,
        rationale=proposal.proposal.rationale,
        expected_outcome=proposal.proposal.expected_outcome,
        applied_edits=applied_edits,
        rollback_of=proposal.rollback_of,
        scope=proposal.rollback_scope or "local",
        created_at=now,
    )


def rollback(
    target: RefinementResult,
    state: HarnessState,
    history: List[RefinementHistoryEntry],
    baseline_state: Optional[HarnessState] = None,
) -> Optional[RefinementResult]:
    """Reverte um refinement aplicado, restaurando baseline_state.

    Se o target já foi revertido, retorna None.
    """
    if target.rollback_of:
        return None
    history_entry = next((h for h in history if h.id == target.id), None)
    if history_entry and history_entry.rolled_back:
        return None

    rollback_edits: List[RefinementEdit] = []
    for edit in reversed(target.applied_edits):
        if not edit.applied:
            continue
        if edit.before is not None:
            if edit.after is not None:
                rollback_edits.append(RefinementEdit(
                    action="update",
                    kind=edit.kind,
                    id=edit.edit_id,
                    title=edit.before.get("title") if isinstance(edit.before, dict) else None,
                    content=edit.before.get("content") if isinstance(edit.before, dict) else None,
                    path=edit.before.get("path") if isinstance(edit.before, dict) else None,
                    reference=edit.before.get("reference") if isinstance(edit.before, dict) else None,
                    arguments=edit.before.get("arguments") if isinstance(edit.before, dict) else None,
                    metadata=edit.before.get("metadata") if isinstance(edit.before, dict) else None,
                    reason=f"Rollback {target.id}",
                ))
            else:
                rollback_edits.append(RefinementEdit(
                    action="create",
                    kind=edit.kind,
                    id=edit.edit_id,
                    title=edit.before.get("title") if isinstance(edit.before, dict) else None,
                    content=edit.before.get("content") if isinstance(edit.before, dict) else None,
                    path=edit.before.get("path") if isinstance(edit.before, dict) else None,
                    reference=edit.before.get("reference") if isinstance(edit.before, dict) else None,
                    arguments=edit.before.get("arguments") if isinstance(edit.before, dict) else None,
                    metadata=edit.before.get("metadata") if isinstance(edit.before, dict) else None,
                    reason=f"Rollback {target.id}",
                ))
        elif edit.after is not None:
            rollback_edits.append(RefinementEdit(
                action="delete",
                kind=edit.kind,
                id=edit.edit_id,
                reason=f"Rollback {target.id}",
            ))

    rollback_proposal = RefinementProposal(
        summary=f"Rollback refinement {target.id}",
        rationale=f"Restores harness state to snapshot before refinement {target.id}.",
        expected_outcome="Faulty refinement edits are reverted.",
        edits=rollback_edits,
    )
    rollback_plan = RefinementPlan(
        proposal=rollback_proposal,
        id=f"rollback_{target.id}",
        rollback_of=target.id,
        rollback_scope=target.scope,
        baseline_state=None,
    )
    result = apply_refinement_proposal(rollback_plan, state, history)
    if history_entry:
        history_entry.rolled_back = True
    return result


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _validate_edit(edit: RefinementEdit, edit_id: str) -> Optional[str]:
    if edit.action not in ("create", "update", "delete"):
        return f"invalid action: {edit.action}"
    if edit.kind not in ("file", "config", "script", "skill", "decision"):
        return f"invalid kind: {edit.kind}"
    if edit.action != "delete" and edit.kind == "skill" and edit.arguments is None:
        return f"{edit.action} skill requires arguments"
    if edit.action != "delete" and edit.kind == "skill":
        if not edit.reference:
            return f"{edit.action} skill requires reference"
        if not isinstance(edit.reference, dict):
            return f"{edit.action} skill reference must be dict"
    return None


# ---------------------------------------------------------------------------
# JSON parsing
# ---------------------------------------------------------------------------


def _parse_proposal(text: str) -> RefinementProposal:
    """Extrai JSON do texto do LLM.

    Tenta extrair o primeiro objeto JSON válido do texto.
    """
    text = text.strip()
    if text.startswith("```json"):
        text = text[7:]
    if text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()

    start = text.find("{")
    if start == -1:
        raise ValueError("No JSON object found in LLM response")
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                json_text = text[start:i + 1]
                break
    else:
        raise ValueError("Unbalanced JSON in LLM response")

    try:
        payload = json.loads(json_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON from LLM: {exc}") from exc

    edits_raw = payload.get("edits", [])
    edits: List[RefinementEdit] = []
    for raw in edits_raw:
        if not isinstance(raw, dict):
            continue
        edits.append(RefinementEdit(
            action=raw.get("action", "update"),
            kind=raw.get("kind", "file"),
            id=raw.get("id"),
            title=raw.get("title"),
            content=raw.get("content"),
            path=raw.get("path"),
            reference=raw.get("reference"),
            arguments=raw.get("arguments"),
            metadata=raw.get("metadata"),
            reason=raw.get("reason"),
        ))

    return RefinementProposal(
        summary=payload.get("summary", "no-op"),
        rationale=payload.get("rationale", ""),
        edits=edits,
        expected_outcome=payload.get("expected_outcome", ""),
    )


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

REFINEMENT_SYSTEM_PROMPT = """You are a refinement planner. Analyze the current harness state,
conversation history, and refinement history to propose concrete, minimal edits
that improve the agent's future behavior. Each edit must be one of: create, update, delete.

Edits are applied to a generic harness state. Use these kinds:
- "file": edits to files (path, content)
- "config": edits to configuration (path, content)
- "script": edits to scripts (path, content)
- "skill": edits to skills (path, content, reference)
- "decision": edits to decisions (title, content)

Rules:
- Propose only edits that are clearly justified by the conversation.
- Do NOT propose session-only progress or temporary blockers for global scope.
- If no edit is justified, return empty edits array with a clear rationale.
- Be minimal: quality > quantity.
- Always return valid JSON matching the requested schema."""


# ---------------------------------------------------------------------------
# RefinementPlan
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RefinementPlan:
    proposal: RefinementProposal
    id: str
    rollback_of: Optional[str] = None
    rollback_scope: str = "local"
    baseline_state: Optional[HarnessState] = None


# ---------------------------------------------------------------------------
# Conveniência
# ---------------------------------------------------------------------------


def summarize_for_refinement(messages: List[Dict[str, Any]], max_chars: int = 40_000) -> str:
    """Resume conversação para usar como contexto de refinement."""
    return _serialize_messages(messages, max_chars=max_chars)
