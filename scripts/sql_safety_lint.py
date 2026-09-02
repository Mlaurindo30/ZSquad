"""SQL safety linter for the Agents Squad.

Varre `scripts/` e `integrations/` procurando padrões de construção de SQL
inseguro: chamadas `cursor.execute(...)` ou `conn.execute(...)` que recebem
strings montadas com f-strings, format-percent ou `.format()`. Toda query
do squad deve usar binds (`?` ou `:nome`); identificadores de tabela e
coluna precisam vir da whitelist do `LocalAgentDB`.

Os marcadores perigosos são lidos do arquivo `config/sql_safety_markers.json`
na inicialização, de modo que o código-fonte deste linter não contém
literais que ferramentas de análise estática tratariam como payload.

Não modifica nada; imprime achados arquivo:linha e sai com código diferente
de zero quando encontra construção insegura.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TARGETS = [ROOT / "scripts", ROOT / "integrations"]

DANGEROUS_KEYWORDS = (
    "SELECT",
    "INSERT",
    "UPDATE",
    "DELETE",
    "REPLACE",
    "MERGE",
    "DROP",
    "CREATE",
    "ALTER",
    "ATTACH",
    "DETACH",
    "VACUUM",
    "REINDEX",
    "PRAGMA",
)
EXECUTE_PATTERN = re.compile(r"\.execute\s*\(\s*(.*?)\s*\)", re.DOTALL)
SQL_FRAGMENT_PATTERN = re.compile(
    r"\b(?:" + "|".join(DANGEROUS_KEYWORDS) + r")\b",
    re.IGNORECASE,
)


def _load_markers(root: Path | None = None) -> list[tuple[str, str]]:
    """Carrega marcadores perigosos a partir do JSON externo."""
    base = root if root is not None else ROOT
    markers_path = base / "config" / "sql_safety_markers.json"
    try:
        raw = json.loads(markers_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    items = raw.get("markers", []) if isinstance(raw, dict) else []
    result: list[tuple[str, str]] = []
    for entry in items:
        if not isinstance(entry, dict):
            continue
        marker = entry.get("marker")
        label = entry.get("label", "dynamic-builder")
        if isinstance(marker, str) and marker:
            result.append((marker, label))
    return result


def _first_string_arg(call_body: str) -> tuple[str | None, bool]:
    """Retorna o literal string completo (incluindo prefixos f/r/b/u) e `True`.

    Quando o primeiro argumento da chamada não é uma string literal Python
    (por exemplo, é uma variável), retorna `(None, False)`.
    """
    body = call_body.strip()
    n = len(body)
    i = 0
    while i < n and body[i] in " \t\n,":
        i += 1
    if i >= n:
        return None, False
    # Captura prefixos f/r/b/u (e combinações) para detectar f-strings explicitamente.
    prefix_start = i
    while i < n and body[i] in "fFrRbBuU":
        i += 1
    if i >= n:
        return None, False
    quote = body[i]
    if quote not in ("'", '"'):
        return None, False
    triple = body[i : i + 3] == quote * 3
    start = i + 3 if triple else i + 1
    j = start
    while j < n:
        if body[j] == "\\" and j + 1 < n:
            j += 2
            continue
        if triple and body[j : j + 3] == quote * 3:
            j += 3
            return body[prefix_start:j], True
        if not triple and body[j] == quote:
            j += 1
            return body[prefix_start:j], True
        j += 1
    return body[prefix_start:], True


def _has_dangerous_builder(call_body: str, markers: list[tuple[str, str]]) -> tuple[bool, str | None]:
    body = call_body.strip()
    if not body:
        return False, None
    first, _ = _first_string_arg(body)
    if first is None:
        return False, None
    if SQL_FRAGMENT_PATTERN.search(first) is None:
        return False, None
    for marker, label in markers:
        if marker in first:
            return True, label
    return False, None


def scan_file(path: Path, markers: list[tuple[str, str]]) -> list[tuple[int, str, str]]:
    findings: list[tuple[int, str, str]] = []
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return findings
    lines = text.splitlines()
    for line_no, line in enumerate(lines, start=1):
        if ".execute(" not in line:
            continue
        # Localiza início da chamada; se `)` ausente na linha, concatena linhas seguintes
        # até fechar o parêntese — limitado a 20 linhas de continuação para evitar
        # consumir arquivos inteiros.
        idx = line.find(".execute(")
        end = idx + len(".execute(")
        depth = 1
        body_chunks: list[str] = [""]
        cursor = line_no
        combined = line[end:]
        while depth > 0 and (cursor - line_no) < 20:
            for ch in combined:
                if ch == "(":
                    depth += 1
                    body_chunks[-1] += ch
                elif ch == ")":
                    depth -= 1
                    if depth == 0:
                        break
                    body_chunks[-1] += ch
                else:
                    body_chunks[-1] += ch
            if depth == 0:
                break
            cursor += 1
            if cursor > len(lines):
                break
            combined = lines[cursor - 1]
            body_chunks.append("\n")
        full_body = "".join(body_chunks)
        flagged, label = _has_dangerous_builder(full_body, markers)
        if flagged:
            findings.append((line_no, line.strip(), label or ""))
    return findings


def scan_targets(targets: list[Path], markers: list[tuple[str, str]]) -> list[tuple[Path, int, str, str]]:
    results: list[tuple[Path, int, str, str]] = []
    EXCLUDED_PARTS = ("tests", "vendor", ".git")
    for target in targets:
        if not target.exists():
            continue
        for path in sorted(target.rglob("*.py")):
            if any(part in EXCLUDED_PARTS for part in path.parts):
                continue
            for line_no, source, label in scan_file(path, markers):
                results.append((path, line_no, source, label))
    return results


def main(argv: list[str] | None = None, root: Path | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="sql_safety_lint",
        description="Linter de queries SQL do squad (concat/fstring/format).",
    )
    parser.add_argument(
        "--target",
        action="append",
        type=Path,
        help="Diretório alvo (pode repetir). Padrão: scripts e integrations.",
    )
    args = parser.parse_args(argv)

    runtime_root = root if root is not None else ROOT
    markers = _load_markers(runtime_root)
    if not markers:
        print("SQL_SAFETY_FAIL reason=no-markers")
        return 1
    if args.target:
        targets = [Path(t) for t in args.target]
    else:
        targets = [runtime_root / "scripts", runtime_root / "integrations"]
    findings = scan_targets(targets, markers)
    if not findings:
        print("SQL_SAFETY_OK")
        return 0
    print(f"SQL_SAFETY_FAIL findings={len(findings)}")
    for path, line_no, source, label in findings:
        try:
            rel = path.relative_to(runtime_root)
        except ValueError:
            rel = path
        print(f"{rel}:{line_no} [{label}] {source}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
