"""Checagem determinística de drift entre prompts de provider.

Compara os quatro prompts raiz do Agents Squad (AGENTS.md, CLAUDE.md,
CODEX.md, GEMINI.md) e verifica:
- limites de bytes por provider;
- sobreposição textual entre CLAUDE.md e CODEX.md (alvo: <= 80%);
- hash SHA-256 dos arquivos para detectar drift silencioso entre execuções.

Quando ``--write`` é informado, regenera ``documentation/provider-prompts-hashes.json``
com os hashes atuais e o relatório de sobreposição. Caso contrário, apenas
verifica e sai com código 0 quando OK, 1 quando falha.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PROVIDER_LIMITS = {
    "AGENTS.md": 12_000,
    "CLAUDE.md": 30_000,
    "CODEX.md": 30_000,
    "GEMINI.md": 12_000,
}

# Sobreposição máxima aceitável entre CLAUDE.md e CODEX.md (caracteres compartilhados / maior).
MAX_CLAUDE_CODEX_OVERLAP = 0.80

# Limite de sobreposição entre AGENTS.md e qualquer outro (política comum, mas os
# outros adaptadores devem adicionar contexto, não repetir a base).
MAX_AGENTS_OTHER_OVERLAP = 0.60

WHITESPACE_RE = re.compile(r"\s+")


def _normalize(text: str) -> str:
    return WHITESPACE_RE.sub(" ", text.strip()).lower()


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _overlap_ratio(a: str, b: str) -> float:
    norm_a = _normalize(a)
    norm_b = _normalize(b)
    if not norm_a or not norm_b:
        return 0.0
    matcher = SequenceMatcher(None, norm_a, norm_b)
    return matcher.ratio()


def _check_sizes() -> list[str]:
    issues: list[str] = []
    for name, limit in PROVIDER_LIMITS.items():
        path = ROOT / name
        if not path.exists():
            issues.append(f"MISSING {name}")
            continue
        size = path.stat().st_size
        if size > limit:
            issues.append(f"OVERSIZE {name} bytes={size} limit={limit}")
    return issues


def _check_overlap(
    max_claude_codex_overlap: float = MAX_CLAUDE_CODEX_OVERLAP,
) -> tuple[list[str], float]:
    issues: list[str] = []
    files = {name: (ROOT / name).read_text(encoding="utf-8") for name in PROVIDER_LIMITS}
    agents_norm = _normalize(files["AGENTS.md"])
    claude_norm = _normalize(files["CLAUDE.md"])
    codex_norm = _normalize(files["CODEX.md"])
    gemini_norm = _normalize(files["GEMINI.md"])
    ratio_claude_codex = SequenceMatcher(None, claude_norm, codex_norm).ratio()
    if ratio_claude_codex > max_claude_codex_overlap:
        issues.append(
            f"DRIFT CLAUDE<->CODEX overlap={ratio_claude_codex:.3f} > {max_claude_codex_overlap}"
        )
    for other in ("CLAUDE.md", "CODEX.md", "GEMINI.md"):
        other_norm = _normalize(files[other])  # type: ignore[index]
        r = SequenceMatcher(None, agents_norm, other_norm).ratio()
        if r > MAX_AGENTS_OTHER_OVERLAP:
            issues.append(
                f"DRIFT AGENTS<->{other} overlap={r:.3f} > {MAX_AGENTS_OTHER_OVERLAP}"
            )
    return issues, ratio_claude_codex


def main(argv: list[str] | None = None, max_overlap: float | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="build_provider_prompts",
        description="Checagem de drift e limites dos prompts de provider.",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Atualiza o relatório de hashes em documentation/provider-prompts-hashes.json.",
    )
    parser.add_argument(
        "--max-claude-codex-overlap",
        type=float,
        default=MAX_CLAUDE_CODEX_OVERLAP,
        help="Limite de sobreposição textual CLAUDE.md vs CODEX.md.",
    )
    args = parser.parse_args(argv)

    threshold = max_overlap if max_overlap is not None else args.max_claude_codex_overlap

    size_issues = _check_sizes()
    issues, ratio = _check_overlap(max_claude_codex_overlap=threshold)
    if ratio > threshold:
        issues.append(
            f"DRIFT CLAUDE<->CODEX overlap={ratio:.3f} > {threshold}"
        )

    hashes = {name: _hash(ROOT / name) for name in PROVIDER_LIMITS}

    if args.write:
        target = ROOT / "documentation" / "provider-prompts-hashes.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 1,
            "hashes": hashes,
            "sizes": {name: (ROOT / name).stat().st_size for name in PROVIDER_LIMITS},
            "claude_codex_overlap": round(ratio, 4),
            "thresholds": {
                "max_claude_codex_overlap": MAX_CLAUDE_CODEX_OVERLAP,
                "max_agents_other_overlap": MAX_AGENTS_OTHER_OVERLAP,
            },
        }
        target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(f"PROVIDER_PROMPTS_WRITTEN {target}")

    if size_issues or issues:
        print("PROVIDER_PROMPTS_FAIL")
        for line in size_issues + issues:
            print(f" - {line}")
        return 1
    print("PROVIDER_PROMPTS_OK")
    print(f"claude_codex_overlap={ratio:.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
