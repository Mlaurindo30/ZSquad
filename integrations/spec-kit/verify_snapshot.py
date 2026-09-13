"""Verificador do snapshot upstream do Spec Kit (Tarefa T1).

Compara a árvore integrations/spec-kit/upstream/ contra o manifesto
UPSTREAM_FILES.sha256 (formato git-style: "<sha256_hex>  <caminho_relativo>",
separador POSIX "/", ordenado por caminho).

Uso como módulo:
    from verify_snapshot import load_manifest, verify_tree
    ok, problems = verify_tree(snapshot_dir, manifest_path)

Uso como CLI:
    python verify_snapshot.py [raiz_snapshot] [manifesto]
    Exit code 0 = snapshot íntegro; 1 = divergência; 2 = uso inválido.

Sem divergência, `verify_tree` retorna (True, []).
Divergências reportadas (problemas são strings):
    - MISSING: arquivo do manifesto ausente na árvore
    - MISMATCH: arquivo presente com hash diferente
    - EXTRA: arquivo presente na árvore e ausente no manifesto
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

DEFAULT_ROOT = Path(__file__).resolve().parent / "upstream"
DEFAULT_MANIFEST = Path(__file__).resolve().parent / "UPSTREAM_FILES.sha256"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_manifest(manifest_path: Path) -> dict[str, str]:
    """Retorna {caminho_relativo_posix: sha256_hex} a partir do manifesto."""
    entries: dict[str, str] = {}
    with manifest_path.open("r", encoding="utf-8") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            line = raw_line.rstrip("\n").rstrip("\r")
            if not line:
                continue
            parts = line.split("  ", 1)
            if len(parts) != 2 or not parts[0] or not parts[1]:
                raise ValueError(
                    f"linha {line_number} do manifesto fora do formato "
                    f"'<sha256>  <caminho>': {line!r}"
                )
            expected_hash, rel_path = parts[0].strip().lower(), parts[1]
            if len(expected_hash) != 64 or any(
                c not in "0123456789abcdef" for c in expected_hash
            ):
                raise ValueError(f"sha256 inválido na linha {line_number}: {parts[0]!r}")
            rel_path = rel_path.replace("\\", "/")
            if rel_path.startswith("/") or ".." in rel_path.split("/"):
                raise ValueError(f"caminho inseguro no manifesto linha {line_number}: {rel_path!r}")
            if rel_path in entries:
                raise ValueError(f"caminho duplicado no manifesto: {rel_path!r}")
            entries[rel_path] = expected_hash
    return entries


def verify_tree(snapshot_dir: Path, manifest_path: Path) -> tuple[bool, list[str]]:
    """Compara a árvore em snapshot_dir com o manifesto.

    Retorna (ok, problems). ok é True somente se não houver
    MISSING/MISMATCH/EXTRA e o manifesto existir e for legível.
    """
    problems: list[str] = []
    if not manifest_path.is_file():
        return False, [f"MISSING: manifesto não encontrado: {manifest_path}"]
    if not snapshot_dir.is_dir():
        return False, [f"MISSING: diretório do snapshot não encontrado: {snapshot_dir}"]

    expected = load_manifest(manifest_path)

    actual_rel_paths: set[str] = set()
    for file_path in snapshot_dir.rglob("*"):
        if file_path.is_symlink():
            problems.append(f"EXTRA: symlink não permitido: {file_path.relative_to(snapshot_dir).as_posix()}")
            continue
        if not file_path.is_file():
            continue
        rel_posix = file_path.relative_to(snapshot_dir).as_posix()
        actual_rel_paths.add(rel_posix)
        expected_hash = expected.get(rel_posix)
        if expected_hash is None:
            problems.append(f"EXTRA: {rel_posix}")
        elif _sha256_file(file_path) != expected_hash:
            problems.append(f"MISMATCH: {rel_posix}")

    for rel_posix in sorted(set(expected) - actual_rel_paths):
        problems.append(f"MISSING: {rel_posix}")

    return (not problems), problems


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) > 2:
        print("uso: python verify_snapshot.py [raiz_snapshot] [manifesto]", file=sys.stderr)
        return 2
    snapshot_dir = Path(argv[0]).resolve() if len(argv) >= 1 else DEFAULT_ROOT
    manifest_path = Path(argv[1]).resolve() if len(argv) >= 2 else DEFAULT_MANIFEST
    ok, problems = verify_tree(snapshot_dir, manifest_path)
    if ok:
        print(f"OK: snapshot íntegro ({manifest_path.name})")
        return 0
    for problem in problems:
        print(problem)
    print(f"FALHA: {len(problems)} divergência(s) entre árvore e manifesto", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
