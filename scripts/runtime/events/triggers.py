"""Deterministic, purely declarative Trigger Registry and Condition Evaluator.

Strictly stdlib-only. ZERO LLM dependencies, ZERO eval/exec invocation.
Uses strict AST parsing with whitelist security validation.
"""

from __future__ import annotations

import ast
from typing import Any, Dict, List, Optional, Set

from scripts.domain.events import DomainEvent, TriggerPolicy
from scripts.runtime.events.errors import TriggerAlreadyExistsError, TriggerConditionError

# Allowed AST node types for safe declarative evaluation
ALLOWED_AST_NODES = (
    ast.Expression,
    ast.BoolOp,
    ast.UnaryOp,
    ast.Compare,
    ast.Name,
    ast.Attribute,
    ast.Subscript,
    ast.Constant,
    ast.List,
    ast.Tuple,
    ast.Set,
    ast.Load,
    # Operators
    ast.And,
    ast.Or,
    ast.Not,
    ast.Eq,
    ast.NotEq,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
    ast.In,
    ast.NotIn,
    ast.Is,
    ast.IsNot,
    ast.USub,
)


def _safe_eval_node(node: ast.AST, context: Dict[str, Any]) -> Any:
    """Recursively and safely evaluates an approved AST node against the evaluation context."""
    if isinstance(node, ast.Expression):
        return _safe_eval_node(node.body, context)

    if isinstance(node, ast.Constant):
        return node.value

    if isinstance(node, ast.Name):
        name = node.id
        if name in ("true", "True"):
            return True
        if name in ("false", "False"):
            return False
        if name in ("null", "none", "None"):
            return None
        return context.get(name)

    if isinstance(node, ast.Attribute):
        # Prevent private/dunder attribute access
        if node.attr.startswith("_"):
            raise TriggerConditionError(
                f"access to private attribute '{node.attr}' is prohibited"
            )
        base_val = _safe_eval_node(node.value, context)
        if base_val is None:
            return None
        if isinstance(base_val, dict):
            return base_val.get(node.attr)
        return getattr(base_val, node.attr, None)

    if isinstance(node, ast.Subscript):
        base_val = _safe_eval_node(node.value, context)
        if base_val is None:
            return None
        slice_val = _safe_eval_node(node.slice, context)
        try:
            return base_val[slice_val]
        except (KeyError, IndexError, TypeError):
            return None

    if isinstance(node, (ast.List, ast.Tuple)):
        return [_safe_eval_node(elem, context) for elem in node.elts]

    if isinstance(node, ast.Set):
        return {_safe_eval_node(elem, context) for elem in node.elts}

    if isinstance(node, ast.UnaryOp):
        operand_val = _safe_eval_node(node.operand, context)
        if isinstance(node.op, ast.Not):
            return not bool(operand_val)
        if isinstance(node.op, ast.USub):
            return -operand_val
        raise TriggerConditionError(f"Unsupported unary operator: {type(node.op).__name__}")

    if isinstance(node, ast.BoolOp):
        if isinstance(node.op, ast.And):
            for val_node in node.values:
                if not _safe_eval_node(val_node, context):
                    return False
            return True
        if isinstance(node.op, ast.Or):
            for val_node in node.values:
                if _safe_eval_node(val_node, context):
                    return True
            return False
        raise TriggerConditionError(f"Unsupported boolean operator: {type(node.op).__name__}")

    if isinstance(node, ast.Compare):
        left_val = _safe_eval_node(node.left, context)
        for op, comparator in zip(node.ops, node.comparators):
            right_val = _safe_eval_node(comparator, context)
            if isinstance(op, ast.Eq):
                matched = left_val == right_val
            elif isinstance(op, ast.NotEq):
                matched = left_val != right_val
            elif isinstance(op, ast.Lt):
                if left_val is None or right_val is None:
                    matched = False
                else:
                    matched = left_val < right_val
            elif isinstance(op, ast.LtE):
                if left_val is None or right_val is None:
                    matched = False
                else:
                    matched = left_val <= right_val
            elif isinstance(op, ast.Gt):
                if left_val is None or right_val is None:
                    matched = False
                else:
                    matched = left_val > right_val
            elif isinstance(op, ast.GtE):
                if left_val is None or right_val is None:
                    matched = False
                else:
                    matched = left_val >= right_val
            elif isinstance(op, ast.In):
                if right_val is None:
                    matched = False
                else:
                    matched = left_val in right_val
            elif isinstance(op, ast.NotIn):
                if right_val is None:
                    matched = True
                else:
                    matched = left_val not in right_val
            elif isinstance(op, ast.Is):
                matched = left_val is right_val
            elif isinstance(op, ast.IsNot):
                matched = left_val is not right_val
            else:
                raise TriggerConditionError(f"Unsupported comparison operator: {type(op).__name__}")

            if not matched:
                return False
            left_val = right_val
        return True

    raise TriggerConditionError(f"Disallowed AST node type: {type(node).__name__}")


def evaluate_condition(condition_expression: str, event: DomainEvent) -> bool:
    """Deterministically evaluates a declarative condition expression against a DomainEvent.

    Guarantees:
    - Zero execution of arbitrary Python code (no eval, no exec).
    - Whitelist AST enforcement: function calls, imports, loops, and dunder attributes are blocked.
    - Missing dictionary or attribute keys resolve safely to None without throwing uncaught errors.
    - Case-insensitive support for wildcards ('*', 'ALL', 'true').

    Args:
        condition_expression: Declarative condition string (e.g., "payload.risk == 'high'").
        event: The canonical DomainEvent to evaluate against.

    Returns:
        bool: True if condition is met or wildcard; False otherwise.

    Raises:
        TriggerConditionError: If syntax is invalid or contains prohibited AST constructs.
    """
    clean_expr = condition_expression.strip() if condition_expression else ""
    if not clean_expr or clean_expr in ("*", "true", "True", "all", "ALL", "1"):
        return True
    if clean_expr in ("false", "False", "0"):
        return False

    try:
        parsed = ast.parse(clean_expr, mode="eval")
    except SyntaxError as err:
        raise TriggerConditionError(clean_expr, f"Syntax error: {err.msg}") from err

    # Validate AST node whitelist
    for node in ast.walk(parsed):
        if not isinstance(node, ALLOWED_AST_NODES):
            raise TriggerConditionError(
                clean_expr,
                f"Prohibited node type '{type(node).__name__}'. Only declarative comparisons are allowed.",
            )

    # Construct context from DomainEvent
    ts_str = event.timestamp.isoformat() if event.timestamp else ""
    context: Dict[str, Any] = {
        "event_id": event.event_id,
        "event_type": event.event_type,
        "work_item_id": event.work_item_id,
        "project_id": event.project_id,
        "source": event.source,
        "correlation_id": event.correlation_id,
        "causation_id": event.causation_id,
        "idempotency_key": event.idempotency_key,
        "timestamp": ts_str,
        "payload": event.payload or {},
    }

    try:
        result = _safe_eval_node(parsed.body, context)
        return bool(result)
    except TriggerConditionError:
        raise
    except Exception as err:
        raise TriggerConditionError(clean_expr, f"Evaluation error: {str(err)}") from err


class TriggerRegistry:
    """Thread-safe in-memory registry of TriggerPolicy definitions."""

    def __init__(self) -> None:
        self._policies_by_id: Dict[str, TriggerPolicy] = {}
        self._policies_by_type: Dict[str, Set[str]] = {}
        self._disabled_trigger_ids: Set[str] = set()

    def register(self, policy: TriggerPolicy, overwrite: bool = False) -> None:
        """Registers a declarative trigger policy.
        
        Raises:
            TriggerAlreadyExistsError: If trigger_id already exists and overwrite is False.
        """
        if policy.trigger_id in self._policies_by_id and not overwrite:
            raise TriggerAlreadyExistsError(policy.trigger_id)

        self._policies_by_id[policy.trigger_id] = policy

        event_type = policy.event_type
        if event_type not in self._policies_by_type:
            self._policies_by_type[event_type] = set()
        self._policies_by_type[event_type].add(policy.trigger_id)

    def disable(self, trigger_id: str) -> None:
        """Disables a registered trigger policy without unregistering it."""
        self._disabled_trigger_ids.add(trigger_id)

    def enable(self, trigger_id: str) -> None:
        """Enables a previously disabled trigger policy."""
        self._disabled_trigger_ids.discard(trigger_id)

    def is_enabled(self, trigger_id: str) -> bool:
        """Checks whether a trigger policy is enabled."""
        return trigger_id in self._policies_by_id and trigger_id not in self._disabled_trigger_ids

    def unregister(self, trigger_id: str) -> bool:
        """Removes a trigger policy by ID. Returns True if removed, False if not found."""
        self._disabled_trigger_ids.discard(trigger_id)
        policy = self._policies_by_id.pop(trigger_id, None)
        if not policy:
            return False

        event_type = policy.event_type
        if event_type in self._policies_by_type:
            self._policies_by_type[event_type].discard(trigger_id)
            if not self._policies_by_type[event_type]:
                del self._policies_by_type[event_type]
        return True

    def get_policy(self, trigger_id: str) -> Optional[TriggerPolicy]:
        """Retrieves a trigger policy by ID."""
        return self._policies_by_id.get(trigger_id)

    def list_policies(self, event_type: Optional[str] = None) -> List[TriggerPolicy]:
        """Lists registered policies, optionally filtered by event_type."""
        if event_type is None:
            return list(self._policies_by_id.values())

        matching_ids = self._policies_by_type.get(event_type, set())
        wildcard_ids = self._policies_by_type.get("*", set())
        all_ids = matching_ids | wildcard_ids
        return [self._policies_by_id[tid] for tid in all_ids if tid in self._policies_by_id]

    def match_triggers(self, event: DomainEvent) -> List[TriggerPolicy]:
        """Evaluates all candidate policies against a domain event and returns matched policies."""
        candidate_ids: Set[str] = set()

        # Match exact event_type
        if event.event_type in self._policies_by_type:
            candidate_ids.update(self._policies_by_type[event.event_type])

        # Match wildcard event_type '*'
        if "*" in self._policies_by_type:
            candidate_ids.update(self._policies_by_type["*"])

        matched: List[TriggerPolicy] = []
        for tid in candidate_ids:
            if tid in self._disabled_trigger_ids:
                continue
            policy = self._policies_by_id.get(tid)
            if not policy:
                continue
            if evaluate_condition(policy.condition_expression, event):
                matched.append(policy)

        return matched

    def clear(self) -> None:
        """Clears all registered policies."""
        self._policies_by_id.clear()
        self._policies_by_type.clear()
        self._disabled_trigger_ids.clear()
