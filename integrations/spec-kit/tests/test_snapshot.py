"""Testes comportamentais do verificador do snapshot Spec Kit (Tarefa T1).

Casos de fixture (tmp_path) cobrem: árvore íntegra passa; byte alterado
rejeita (MISMATCH); arquivo extra não manifestado rejeita (EXTRA); arquivo
ausente rejeita (MISSING). O último teste valida a árvore REAL em
integrations/spec-kit/upstream contra o manifesto real.
"""

from __future__ import annotations

import hashlib
import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
VERIFY_SCRIPT = REPO_ROOT / "integrations" / "spec-kit" / "verify_snapshot.py"
REAL_SNAPSHOT = REPO_ROOT / "integrations" / "spec-kit" / "upstream"
REAL_MANIFEST = REPO_ROOT / "integrations" / "spec-kit" / "UPSTREAM_FILES.sha256"


def _load_verifier():
    """Importa verify_snapshot.py por caminho absoluto ('spec-kit' tem hífen)."""
    spec = importlib.util.spec_from_file_location("verify_snapshot", VERIFY_SCRIPT)
    if spec is None or spec.loader is None:  # pragma: no cover - only if file missing
        pytest.fail(f"verify_snapshot.py não encontrado em {VERIFY_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["verify_snapshot"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def verifier():
    return _load_verifier()


def _write_tree(root: Path, files: dict[str, bytes]) -> None:
    for rel_path, content in files.items():
        target = root / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)


def _write_manifest(root: Path, files: dict[str, bytes]) -> Path:
    lines = []
    for rel_path in sorted(files):
        digest = hashlib.sha256(files[rel_path]).hexdigest()
        lines.append(f"{digest}  {rel_path}")
    manifest = root / "MANIFEST.sha256"
    manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return manifest


FAKE_FILES = {
    "LICENSE": b"MIT License\n",
    "src/tool.py": b"print('hello')\n",
    "docs/guide.md": b"# Guide\n",
}


def test_matching_tree_passes(verifier, tmp_path):
    snapshot = tmp_path / "upstream"
    _write_tree(snapshot, FAKE_FILES)
    manifest = _write_manifest(tmp_path, FAKE_FILES)
    ok, problems = verifier.verify_tree(snapshot, manifest)
    assert ok, problems
    assert problems == []


def test_altered_file_is_rejected(verifier, tmp_path):
    snapshot = tmp_path / "upstream"
    _write_tree(snapshot, FAKE_FILES)
    manifest = _write_manifest(tmp_path, FAKE_FILES)
    # Corrompe um byte de um arquivo existente.
    target = snapshot / "src" / "tool.py"
    target.write_bytes(b"print('HELLO')\n")
    ok, problems = verifier.verify_tree(snapshot, manifest)
    assert not ok
    assert problems == ["MISMATCH: src/tool.py"]


def test_extra_unmanifested_file_is_rejected(verifier, tmp_path):
    snapshot = tmp_path / "upstream"
    _write_tree(snapshot, FAKE_FILES)
    manifest = _write_manifest(tmp_path, FAKE_FILES)
    extra = snapshot / "docs" / "extra.md"
    extra.write_text("não manifestado\n", encoding="utf-8")
    ok, problems = verifier.verify_tree(snapshot, manifest)
    assert not ok
    assert problems == ["EXTRA: docs/extra.md"]


def test_missing_file_is_rejected(verifier, tmp_path):
    snapshot = tmp_path / "upstream"
    manifest_files = dict(FAKE_FILES)
    _write_tree(snapshot, {k: v for k, v in manifest_files.items() if k != "LICENSE"})
    manifest = _write_manifest(tmp_path, manifest_files)
    ok, problems = verifier.verify_tree(snapshot, manifest)
    assert not ok
    assert problems == ["MISSING: LICENSE"]


def test_real_snapshot_has_zero_divergence(verifier):
    """Árvore real integrations/spec-kit/upstream x manifesto real."""
    assert VERIFY_SCRIPT.is_file(), f"verificador ausente: {VERIFY_SCRIPT}"
    assert REAL_MANIFEST.is_file(), f"manifesto ausente: {REAL_MANIFEST}"
    assert REAL_SNAPSHOT.is_dir(), f"snapshot ausente: {REAL_SNAPSHOT}"
    ok, problems = verifier.verify_tree(REAL_SNAPSHOT, REAL_MANIFEST)
    assert ok, problems


# --- caminhos de erro do manifesto e da CLI (robustez exigida pelo plano T1) ---


def test_manifest_malformed_line_rejected(verifier, tmp_path):
    _write_tree(tmp_path / "upstream", FAKE_FILES)
    manifest = tmp_path / "MANIFEST.sha256"
    manifest.write_text("somente-um-campo\n", encoding="utf-8")
    with pytest.raises(ValueError, match="fora do formato"):
        verifier.verify_tree(tmp_path / "upstream", manifest)


def test_manifest_invalid_sha256_rejected(verifier, tmp_path):
    _write_tree(tmp_path / "upstream", FAKE_FILES)
    manifest = tmp_path / "MANIFEST.sha256"
    manifest.write_text("z" * 64 + "  LICENSE\n", encoding="utf-8")
    with pytest.raises(ValueError, match="sha256 inválido"):
        verifier.verify_tree(tmp_path / "upstream", manifest)


@pytest.mark.parametrize("bad_path", ["/absoluto/LICENSE", "../fuga/LICENSE", "a/../../b"])
def test_manifest_unsafe_path_rejected(verifier, tmp_path, bad_path):
    _write_tree(tmp_path / "upstream", FAKE_FILES)
    manifest = tmp_path / "MANIFEST.sha256"
    manifest.write_text("a" * 64 + f"  {bad_path}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="caminho inseguro"):
        verifier.verify_tree(tmp_path / "upstream", manifest)


def test_manifest_duplicate_path_rejected(verifier, tmp_path):
    digest = hashlib.sha256(b"MIT License\n").hexdigest()
    manifest = tmp_path / "MANIFEST.sha256"
    manifest.write_text(f"{digest}  LICENSE\n{digest}  LICENSE\n", encoding="utf-8")
    _write_tree(tmp_path / "upstream", FAKE_FILES)
    with pytest.raises(ValueError, match="caminho duplicado"):
        verifier.verify_tree(tmp_path / "upstream", manifest)


def test_missing_manifest_is_rejected(verifier, tmp_path):
    _write_tree(tmp_path / "upstream", FAKE_FILES)
    ok, problems = verifier.verify_tree(tmp_path / "upstream", tmp_path / "ausente.sha256")
    assert not ok
    assert problems and problems[0].startswith("MISSING: manifesto")


def test_missing_snapshot_dir_is_rejected(verifier, tmp_path):
    manifest = _write_manifest(tmp_path, FAKE_FILES)
    ok, problems = verifier.verify_tree(tmp_path / "inexistente", manifest)
    assert not ok
    assert problems and problems[0].startswith("MISSING: diretório")


@pytest.mark.skipif(not hasattr(__import__("os"), "symlink"), reason="symlink indisponível")
def test_symlink_in_snapshot_is_rejected(verifier, tmp_path):
    import os

    snapshot = tmp_path / "upstream"
    _write_tree(snapshot, FAKE_FILES)
    manifest = _write_manifest(tmp_path, FAKE_FILES)
    link = snapshot / "docs" / "atalho.md"
    try:
        os.symlink(snapshot / "docs" / "guide.md", link)
    except OSError:
        pytest.skip("criação de symlink não permitida neste ambiente")
    ok, problems = verifier.verify_tree(snapshot, manifest)
    assert not ok
    assert "EXTRA: symlink não permitido: docs/atalho.md" in problems


def test_main_cli_success_tamper_and_usage(verifier, tmp_path, capsys):
    snapshot = tmp_path / "upstream"
    _write_tree(snapshot, FAKE_FILES)
    manifest = _write_manifest(tmp_path, FAKE_FILES)
    assert verifier.main([str(snapshot), str(manifest)]) == 0
    # Uso inválido: mais de dois argumentos -> exit 2.
    assert verifier.main(["a", "b", "c"]) == 2
    # Adulteração -> exit 1 e divergência reportada.
    (snapshot / "LICENSE").write_bytes(b"MIT License X\n")
    assert verifier.main([str(snapshot), str(manifest)]) == 1
    captured = capsys.readouterr()
    assert "MISMATCH: LICENSE" in captured.out
    assert "FALHA: 1 divergência" in captured.err
