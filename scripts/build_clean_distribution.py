"""
O que é: Construtor de pacote de distribuição e arquivo não destrutivo.
Responsabilidade: Validar o manifesto de arquivos e materializar a distribuição autorizada.
Pra que serve: Empacotar versões limpas do squad para distribuição.
Comportamento em falha: Dispara PackageError determinístico e interrompe o empacotamento.
Conexões: Lê contracts/distribution-file.schema.json e distribution/files.manifest.jsonl.
Dependências & Imports:
  - json, shutil, hashlib, pathlib: Operações de arquivo e hashing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any


BLOCKED_PREFIXES = ("work/", "archive/", "distribution/materialized/", ".temp/", ".pytest_cache/")
BLOCKED_PARTS = {"__pycache__", ".git"}
BLOCKED_SUFFIXES = {".pyc", ".pyo"}
RECORD_FIELDS = {"path", "sha256", "size", "destination", "classification", "treatment", "lifecycle", "origin", "source_revision", "executable", "license", "sensitivity", "owner", "reason"}


class PackageError(RuntimeError):
    """Exceção levantada quando ocorre um erro na validação ou empacotamento da distribuição."""
    pass


@dataclass(frozen=True)
class PackagePlan:
    """Representa o plano imutável de empacotamento de distribuição e arquivo."""
    distribution: tuple[dict[str, Any], ...]
    archive: tuple[dict[str, Any], ...]
    excluded: tuple[dict[str, Any], ...]
    counts: dict[str, int]


def validate_record_path(value: str) -> str:
    """Valida se o caminho do registro é relativo e formato POSIX válido."""
    if not value or "\\" in value or value.startswith("/") or re.match(r"^[A-Za-z]:", value):
        raise PackageError(f"path must be relative POSIX: {value!r}")
    pure = PurePosixPath(value)
    if pure.is_absolute() or ".." in pure.parts or any(part in {"", "."} for part in pure.parts):
        raise PackageError(f"path must be relative POSIX: {value!r}")
    return pure.as_posix()


def load_manifest(path: Path) -> list[dict[str, Any]]:
    """Carrega e valida os registros de arquivo a partir de um manifesto JSONL."""
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise PackageError(f"invalid manifest JSON line {number}: {exc}") from exc
        if set(record) != RECORD_FIELDS:
            raise PackageError(f"manifest fields mismatch line {number}")
        relative = validate_record_path(record["path"])
        if relative in seen:
            raise PackageError(f"duplicate manifest path: {relative}")
        seen.add(relative)
        if record["destination"] not in {"distribution", "archive", "excluded"}:
            raise PackageError(f"invalid destination: {relative}")
        if not re.fullmatch(r"[a-f0-9]{64}", record["sha256"]):
            raise PackageError(f"invalid sha256: {relative}")
        records.append(record)
    return records


def forbidden_runtime_path(relative: str) -> bool:
    """Verifica se um caminho relativo pertence à lista de caminhos bloqueados de runtime."""
    pure = PurePosixPath(relative)
    return relative.startswith(BLOCKED_PREFIXES) or any(part in BLOCKED_PARTS for part in pure.parts) or pure.suffix.lower() in BLOCKED_SUFFIXES


def _source(root: Path, relative: str) -> Path:
    target = root / Path(*PurePosixPath(relative).parts)
    if target.is_symlink():
        raise PackageError(f"symlink source is forbidden: {relative}")
    if not target.is_file():
        raise PackageError(f"source missing: {relative}")
    resolved = target.resolve(strict=True)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise PackageError(f"source escapes root: {relative}") from exc
    return target


def source_for_record(root: Path, record: dict[str, Any]) -> Path:
    """Resolve o arquivo de origem para um registro do manifesto de distribuição."""
    relative = record["path"]
    current = root / Path(*PurePosixPath(relative).parts)
    archived = root / "archive/materialized" / Path(*PurePosixPath(relative).parts)
    if current.is_file():
        return _source(root, relative)
    if record["destination"] == "archive" and archived.is_file():
        archived_payload = archived.read_bytes()
        archived_digest = hashlib.sha256(archived_payload).hexdigest()
        if archived_digest != record["sha256"] or len(archived_payload) != record["size"]:
            raise PackageError(f"retained archive hash mismatch: {relative}")
        return archived
    return _source(root, relative)


def _verify_source(root: Path, record: dict[str, Any]) -> None:
    target = source_for_record(root, record)
    payload = target.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    if digest != record["sha256"] or len(payload) != record["size"]:
        raise PackageError(f"source hash mismatch: {record['path']} expected={record['sha256']} actual={digest}")


def plan(root: Path, records: list[dict[str, Any]]) -> PackagePlan:
    """Gera o plano de empacotamento separando arquivos entre distribuição, arquivo e exclusão."""
    root = root.resolve(strict=True)
    buckets: dict[str, list[dict[str, Any]]] = {"distribution": [], "archive": [], "excluded": []}
    seen: set[str] = set()
    for record in sorted(records, key=lambda item: item["path"].encode("utf-8")):
        relative = validate_record_path(record["path"])
        if relative in seen:
            raise PackageError(f"duplicate manifest path: {relative}")
        seen.add(relative)
        destination = record["destination"]
        if destination not in buckets:
            raise PackageError(f"invalid destination: {relative}")
        if destination == "distribution" and forbidden_runtime_path(relative):
            raise PackageError(f"forbidden runtime path: {relative}")
        # Transient excluded records are provenance-only. They are intentionally
        # allowed to drift or disappear between snapshot and packaging because
        # they are never copied into either output.
        if record["destination"] != "excluded":
            _verify_source(root, record)
        buckets[destination].append(record)
    if sum(len(value) for value in buckets.values()) != len(records):
        raise PackageError("incomplete manifest reconciliation")
    return PackagePlan(*(tuple(buckets[name]) for name in ("distribution", "archive", "excluded")), {name: len(buckets[name]) for name in buckets})


def _ensure_governed_output(root: Path, output: Path, expected: str, *, allow_existing: bool = False) -> Path:
    output = output.resolve(strict=False)
    try:
        relative = output.relative_to(root).as_posix()
    except ValueError as exc:
        raise PackageError(f"output escapes root: {output}") from exc
    if relative != expected:
        raise PackageError(f"output must be {expected}: {relative}")
    if output.exists() and not allow_existing:
        raise PackageError(f"output already exists: {relative}")
    return output


def _copy_bucket(root: Path, records: tuple[dict[str, Any], ...], staging: Path) -> None:
    for record in records:
        source = source_for_record(root, record)
        target = staging / Path(*PurePosixPath(record["path"]).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target, follow_symlinks=False)
        payload = target.read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        if digest != record["sha256"] or len(payload) != record["size"]:
            raise PackageError(f"destination hash mismatch: {record['path']}")


def _make_archive_read_only(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_file():
            path.chmod(stat.S_IREAD)


def _writable_and_retry(function, path, _exc_info) -> None:
    """Make a read-only path writable before retrying its failed removal."""
    os.chmod(path, stat.S_IWRITE)
    function(path)


def _remove_tree(path: Path) -> None:
    """Remove a governed tree, including read-only files on Windows."""
    try:
        shutil.rmtree(path, onexc=_writable_and_retry)
    except TypeError:
        shutil.rmtree(path, onerror=_writable_and_retry)


def _cleanup_residuals(parent: Path) -> None:
    """Remove only tool-owned staging/previous siblings after a verified commit."""
    for candidate in sorted(parent.glob(".materialized.*")):
        if candidate.name.endswith((".staging", ".previous")) and candidate.is_dir():
            _remove_tree(candidate)


def verify_materialized(plan_value: PackagePlan, output: Path, archive: Path) -> None:
    """Verifica se a distribuição materializada corresponde ao plano e ao manifesto."""
    for base, records in ((output, plan_value.distribution), (archive, plan_value.archive)):
        actual = sorted(path.relative_to(base).as_posix() for path in base.rglob("*") if path.is_file())
        expected = sorted(record["path"] for record in records)
        if actual != expected:
            raise PackageError(f"materialized reconciliation mismatch: {base}")
        for record in records:
            payload = (base / Path(*PurePosixPath(record["path"]).parts)).read_bytes()
            if hashlib.sha256(payload).hexdigest() != record["sha256"] or len(payload) != record["size"]:
                raise PackageError(f"materialized hash mismatch: {record['path']}")


def materialize(root: Path, plan_value: PackagePlan, output: Path, archive: Path) -> dict[str, Any]:
    """Materializa os arquivos de distribuição e arquivo a partir do plano gerado."""
    root = root.resolve(strict=True)
    output = _ensure_governed_output(root, output, "distribution/materialized", allow_existing=True)
    archive = _ensure_governed_output(root, archive, "archive/materialized", allow_existing=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    archive.parent.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    output_stage = output.parent / f".materialized.{token}.staging"
    archive_stage = archive.parent / f".materialized.{token}.staging"
    if output_stage.exists() or archive_stage.exists():
        raise PackageError("staging path collision")
    output_stage.mkdir()
    archive_stage.mkdir()
    try:
        _copy_bucket(root, plan_value.distribution, output_stage)
        _copy_bucket(root, plan_value.archive, archive_stage)
        verify_materialized(plan_value, output_stage, archive_stage)
        old_output = output.parent / f".materialized.{token}.previous"
        old_archive = archive.parent / f".materialized.{token}.previous"
        if output.exists():
            os.replace(output, old_output)
        if archive.exists():
            os.replace(archive, old_archive)
        try:
            os.replace(output_stage, output)
            os.replace(archive_stage, archive)
        except Exception:
            if output.exists():
                _remove_tree(output)
            if archive.exists():
                _remove_tree(archive)
            if old_output.exists():
                os.replace(old_output, output)
            if old_archive.exists():
                os.replace(old_archive, archive)
            raise
        if old_output.exists():
            _remove_tree(old_output)
        if old_archive.exists():
            _remove_tree(old_archive)
        _make_archive_read_only(archive)
        verify_materialized(plan_value, output, archive)
        _cleanup_residuals(output.parent)
        _cleanup_residuals(archive.parent)
    except Exception:
        if output_stage.exists():
            _remove_tree(output_stage)
        if archive_stage.exists():
            _remove_tree(archive_stage)
        raise
    return {"distribution": len(plan_value.distribution), "archive": len(plan_value.archive), "excluded": len(plan_value.excluded), "source_preserved": True}


def serialize_archive_manifest(records: tuple[dict[str, Any], ...]) -> bytes:
    """Serializa os registros do manifesto de arquivo em formato JSONL ordenado."""
    return b"".join((json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8") for record in sorted(records, key=lambda item: item["path"].encode("utf-8")))


def main(argv: list[str] | None = None) -> int:
    """Ponto de entrada CLI para empacotamento da distribuição limpa."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--archive-output", required=True, type=Path)
    parser.add_argument("--archive-manifest", required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args(argv)
    root = args.root.resolve(strict=True)
    records = load_manifest(args.manifest)
    package_plan = plan(root, records)
    if args.dry_run:
        print(json.dumps({"result": "DRY_RUN_OK", "counts": package_plan.counts}, sort_keys=True))
        return 0
    if args.verify:
        verify_materialized(package_plan, args.output, args.archive_output)
        print(json.dumps({"result": "VERIFY_OK", "counts": package_plan.counts}, sort_keys=True))
        return 0
    result = materialize(root, package_plan, args.output, args.archive_output)
    archive_manifest_path = args.archive_manifest.resolve(strict=False)
    expected_manifest = (root / "archive/archive-manifest.jsonl").resolve(strict=False)
    if archive_manifest_path != expected_manifest:
        raise PackageError("archive manifest must use archive/archive-manifest.jsonl")
    archive_manifest_path.parent.mkdir(parents=True, exist_ok=True)
    archive_manifest_path.write_bytes(serialize_archive_manifest(package_plan.archive))
    print(json.dumps({"result": "PACKAGE_OK", **result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
