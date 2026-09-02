#!/usr/bin/env python3
"""toon integration skill.

O que é: skill de integração para codificar/decoidar dados em formato TOON.
Responsabilidade: fornecer funções Python para converter dicionários/listas YAML/JSON em TOON e vice-versa, usada por engines que produzem/consomem metadados.
Pra que serve: reduzir tokens em saídas de catalog, manifestos, audit e handoffs.
Comportamento em falha: retorna texto original; não levanta exceção.
Conexões: integrations/, usado por auto_skill_learner, gate_validators, handoff_toolkit.
Dependências & Imports:
  - pathlib, json, re: path, parse, regex.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


def dumps(obj: Any, *, indent: int = 2) -> str:
    """Serializa objeto Python para formato TOON compacto."""
    if isinstance(obj, dict):
        lines: list[str] = []
        for key, value in obj.items():
            lines.append(f"{key}={_to_value(value)}")
        return "\n".join(lines)
    if isinstance(obj, list):
        return "\n".join(f"- {_to_value(item)}" for item in obj)
    return str(obj)


def loads(text: str) -> Any:
    """Decodifica texto TOON para objeto Python (best-effort)."""
    if not text.strip():
        return {}
    result: dict[str, Any] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("- "):
            continue
        if "=" in line:
            key, value = line.split("=", 1)
            result[key.strip()] = _parse_value(value.strip())
    return result


def _to_value(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return "{" + dumps(value, indent=0).replace("\n", "; ") + "}"
    if isinstance(value, list):
        return "[" + ", ".join(_to_value(v) for v in value) + "]"
    return str(value)


def _parse_value(value: str) -> Any:
    if value == "null":
        return None
    if value == "true":
        return True
    if value == "false":
        return False
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1]
        if not inner.strip():
            return []
        return [_parse_value(v.strip()) for v in inner.split(",")]
    if value.startswith("{") and value.endswith("}"):
        inner = value[1:-1]
        result: dict[str, Any] = {}
        for pair in inner.split(";"):
            pair = pair.strip()
            if "=" in pair:
                k, v = pair.split("=", 1)
                result[k.strip()] = _parse_value(v.strip())
        return result
    if value.startswith('"') and value.endswith('"'):
        return value[1:-1]
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    return value


def optimize_skill_metadata(metadata: dict[str, Any]) -> str:
    """Compacta metadados de skill para exibição em catálogo ou prompt."""
    slim = {
        "name": metadata.get("name"),
        "path": metadata.get("path"),
        "domain": metadata.get("domain"),
        "assigned_to": metadata.get("assigned_to", []),
        "load": metadata.get("load", "on-demand"),
    }
    return dumps(slim)
