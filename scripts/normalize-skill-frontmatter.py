"""Normalize active SKILL.md frontmatter without discarding provenance.

O que é: utilitário de manutenção do parque local de skills.
Responsabilidade: manter somente chaves compatíveis no frontmatter e preservar
metadados legados dentro de ``metadata``.
Pra que serve: permitir que todas as skills sejam carregadas por runtimes que
implementam o contrato Agent Skills.
Comportamento em falha: não altera o arquivo quando o YAML é inválido e encerra
com erro; ``--check`` nunca escreve.
Conexões: usado por ``scripts/verify.ps1`` e pelo curador ao promover skills.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Any

import yaml


ALLOWED = {"name", "description", "license", "allowed-tools", "metadata"}
FRONTMATTER = re.compile(r"\A---\r?\n(.*?)\r?\n---(?=\r?\n|\Z)", re.DOTALL)


def normalize(path: Path, *, check: bool) -> bool:
    """Normaliza e padroniza a ordem e estrutura das chaves do frontmatter YAML de um arquivo SKILL.md."""
    raw = path.read_text(encoding="utf-8")
    match = FRONTMATTER.match(raw)
    if not match:
        raise ValueError(f"frontmatter ausente ou inválido: {path}")
    value = yaml.safe_load(match.group(1))
    if not isinstance(value, dict):
        raise ValueError(f"frontmatter não é um objeto: {path}")

    metadata = value.get("metadata") or {}
    if not isinstance(metadata, dict):
        metadata = {"legacy_metadata": metadata}
    for key in list(value):
        if key not in ALLOWED:
            metadata[key] = value.pop(key)
    if metadata:
        value["metadata"] = metadata

    ordered: dict[str, Any] = {}
    for key in ("name", "description", "license", "allowed-tools", "metadata"):
        if key in value:
            ordered[key] = value[key]
    rendered = "---\n" + yaml.safe_dump(
        ordered,
        allow_unicode=True,
        sort_keys=False,
        width=1000,
    ).rstrip() + "\n---"
    updated = rendered + raw[match.end() :]
    changed = updated != raw
    if changed and not check:
        path.write_text(updated, encoding="utf-8")
    return changed


def active_skills(root: Path) -> list[Path]:
    """Lista skills ativas, excluindo intake, quarentena e vendor."""
    return [
        path
        for path in sorted((root / "skills").rglob("SKILL.md"))
        if "discovery/intake" not in path.as_posix()
        and "discovery/quarantine" not in path.as_posix()
    ]


def main() -> int:
    """Ponto de entrada CLI para normalização de frontmatter das skills."""
    parser = argparse.ArgumentParser(description="Normaliza frontmatter das skills ativas")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    changed = [path for path in active_skills(args.root) if normalize(path, check=args.check)]
    if args.check and changed:
        for path in changed:
            print(f"FRONTMATTER_NEEDS_NORMALIZATION {path.relative_to(args.root)}")
        return 1
    print(f"FRONTMATTER_OK active={len(active_skills(args.root))} changed={len(changed)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
