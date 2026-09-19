"""
Targeted Verification Suite for P0.1 Supply-Chain & Reproducibility (Items A, B, C).

Agile Testing Quadrant: Q4 (Security, Integrity, Supply-Chain Verification)
Authors: Lisa Crispin & Janet Gregory (11-test-engineer)

Items:
- A: Pinned CBM successful download and hash verification.
- B: CBM corrupted archive / hash mismatch (fails closed, refuses extraction/execution, returns non-zero).
- C: CBM missing expected executable member (fails closed, refuses execution, returns non-zero).
"""

from __future__ import annotations

import hashlib
import io
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

import scripts.setup_environment as se


class TestCBMSupplyChainVerification:
    """Verificação direcionada da integridade de supply chain para codebase-memory-mcp (P0.1 A, B, C)."""

    def test_item_a_cbm_constants_and_contract(self):
        """Item A: Valida que a versão, URL e hash SHA-256 estão devidamente pinados de forma imutável."""
        assert se.CODEBASE_MEMORY_VERSION == "0.11.0"
        assert se.CODEBASE_MEMORY_ARCHIVE_SHA256 == "6eb6beaf261b19e419766e78baf93cbc3cf1c6338cff8fb7c0234859f96d1685"
        assert se.CODEBASE_MEMORY_RELEASE_URL == (
            "https://github.com/DeusData/codebase-memory-mcp/releases/download/v0.11.0/codebase-memory-mcp-windows-amd64.zip"
        )
        assert len(se.CODEBASE_MEMORY_ARCHIVE_SHA256) == 64

    def test_item_a_pinned_cbm_successful_download_and_hash_verification(self, monkeypatch):
        """Item A: Simulação de download bem-sucedido com integridade de hash SHA-256 e extração do binário."""
        with tempfile.TemporaryDirectory() as tmp_dir_str:
            vendor_root = Path(tmp_dir_str) / "integrations" / "vendor"
            cbm_dir = vendor_root / "codebase-memory-mcp"
            cbm_dir.mkdir(parents=True, exist_ok=True)
            target_bin = cbm_dir / "build" / "c" / ("codebase-memory-mcp.exe" if se.sys.platform == "win32" else "codebase-memory-mcp")

            # Cria arquivo zip válido contendo o executável esperado
            zip_buf = io.BytesIO()
            with zipfile.ZipFile(zip_buf, "w") as zf:
                zf.writestr("codebase-memory-mcp.exe", b"GENUINE_CBM_BINARY_PAYLOAD")
                zf.writestr("codebase-memory-mcp", b"GENUINE_CBM_BINARY_PAYLOAD")
            zip_bytes = zip_buf.getvalue()
            computed_sha = hashlib.sha256(zip_bytes).hexdigest()

            class FakeResponse:
                def read(self):
                    return zip_bytes
                def __enter__(self):
                    return self
                def __exit__(self, *args):
                    pass

            monkeypatch.setattr(se.urllib.request, "urlopen", lambda req, timeout=None: FakeResponse())
            monkeypatch.setattr(se, "CODEBASE_MEMORY_ARCHIVE_SHA256", computed_sha)

            # Mock smoke probe (--version) com sucesso
            mock_smoke = SimpleNamespace(returncode=0, stdout="codebase-memory-mcp 0.11.0", stderr="")
            monkeypatch.setattr(se.subprocess, "run", lambda cmd, *a, **k: mock_smoke)

            # Executa provision_codebase_memory
            res = se.provision_codebase_memory(vendor_root)

            assert res is True
            assert target_bin.is_file()
            assert target_bin.read_bytes() == b"GENUINE_CBM_BINARY_PAYLOAD"

    def test_item_b_cbm_corrupted_archive_hash_mismatch_fails_closed(self, monkeypatch):
        """Item B: Archive corrompido / divergência de hash SHA-256 falha fechado (refusa extração e retorna False)."""
        with tempfile.TemporaryDirectory() as tmp_dir_str:
            vendor_root = Path(tmp_dir_str) / "integrations" / "vendor"
            cbm_dir = vendor_root / "codebase-memory-mcp"
            cbm_dir.mkdir(parents=True, exist_ok=True)
            target_bin = cbm_dir / "build" / "c" / ("codebase-memory-mcp.exe" if se.sys.platform == "win32" else "codebase-memory-mcp")

            corrupted_payload = b"CORRUPTED_TAMPERED_ZIP_PAYLOAD_NOT_MATCHING_SHA256"

            class FakeCorruptedResponse:
                def read(self):
                    return corrupted_payload
                def __enter__(self):
                    return self
                def __exit__(self, *args):
                    pass

            monkeypatch.setattr(se.urllib.request, "urlopen", lambda req, timeout=None: FakeCorruptedResponse())
            # Hash esperado é o oficial pinado, mas os bytes recebidos são corrompidos
            monkeypatch.setattr(se, "CODEBASE_MEMORY_ARCHIVE_SHA256", "6eb6beaf261b19e419766e78baf93cbc3cf1c6338cff8fb7c0234859f96d1685")

            res = se.provision_codebase_memory(vendor_root)

            # Deve falhar fechado: retorna False
            assert res is False
            # NENHUM binário deve ser extraído ou criado no disco
            assert not target_bin.exists()

    def test_item_b_cbm_corrupted_archive_causes_installer_nonzero_exit(self, tmp_path, monkeypatch):
        """Item B: Falha na integridade SHA-256 do CBM força saída com erro não-zero (exit code 1) do instalador."""
        monkeypatch.setattr(se.sys, "argv", ["setup_environment.py", "--skip-docker", "--skip-tests"])
        monkeypatch.setattr(se, "ROOT", tmp_path)
        monkeypatch.setattr(se, "sync_vendor_repositories", lambda *a, **k: True)
        v_dir = tmp_path / "integrations" / "vendor"
        for r in se.CANONICAL_VENDOR_REPOSITORIES:
            (v_dir / r).mkdir(parents=True, exist_ok=True)

        monkeypatch.setattr(se, "ensure_virtualenv", lambda: Path(se.sys.executable))
        monkeypatch.setattr(se, "init_database", lambda: None)
        # Força retorno False devido ao hash mismatch
        monkeypatch.setattr(se, "provision_codebase_memory", lambda *a: False)

        with pytest.raises(SystemExit) as exc_info:
            se.main()

        assert exc_info.value.code == 1

    def test_item_c_cbm_missing_expected_executable_member_fails_closed(self, monkeypatch):
        """Item C: Arquivo zip íntegro quanto ao hash, mas sem o executável esperado, falha fechado."""
        with tempfile.TemporaryDirectory() as tmp_dir_str:
            vendor_root = Path(tmp_dir_str) / "integrations" / "vendor"
            cbm_dir = vendor_root / "codebase-memory-mcp"
            cbm_dir.mkdir(parents=True, exist_ok=True)
            target_bin = cbm_dir / "build" / "c" / ("codebase-memory-mcp.exe" if se.sys.platform == "win32" else "codebase-memory-mcp")

            # Zip válido, mas sem o executável esperado (apenas outros arquivos)
            zip_buf = io.BytesIO()
            with zipfile.ZipFile(zip_buf, "w") as zf:
                zf.writestr("unexpected_file.txt", b"NO_CBM_BINARY_HERE")
                zf.writestr("malicious_script.sh", b"echo evil")
            zip_bytes = zip_buf.getvalue()
            matching_sha = hashlib.sha256(zip_bytes).hexdigest()

            class FakeResponse:
                def read(self):
                    return zip_bytes
                def __enter__(self):
                    return self
                def __exit__(self, *args):
                    pass

            monkeypatch.setattr(se.urllib.request, "urlopen", lambda req, timeout=None: FakeResponse())
            monkeypatch.setattr(se, "CODEBASE_MEMORY_ARCHIVE_SHA256", matching_sha)

            res = se.provision_codebase_memory(vendor_root)

            # Deve falhar fechado: recusa extração e retorna False
            assert res is False
            # O alvo não foi criado
            assert not target_bin.exists()

    def test_item_c_cbm_missing_executable_causes_installer_nonzero_exit(self, tmp_path, monkeypatch):
        """Item C: Zip sem executável esperado faz com que o instalador aborte com exit code 1."""
        monkeypatch.setattr(se.sys, "argv", ["setup_environment.py", "--skip-docker", "--skip-tests"])
        monkeypatch.setattr(se, "ROOT", tmp_path)
        monkeypatch.setattr(se, "sync_vendor_repositories", lambda *a, **k: True)
        v_dir = tmp_path / "integrations" / "vendor"
        for r in se.CANONICAL_VENDOR_REPOSITORIES:
            (v_dir / r).mkdir(parents=True, exist_ok=True)

        monkeypatch.setattr(se, "ensure_virtualenv", lambda: Path(se.sys.executable))
        monkeypatch.setattr(se, "init_database", lambda: None)
        # Simula provision_codebase_memory retornando False por membro ausente
        monkeypatch.setattr(se, "provision_codebase_memory", lambda *a: False)

        with pytest.raises(SystemExit) as exc_info:
            se.main()

        assert exc_info.value.code == 1
