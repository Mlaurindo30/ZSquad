#!/usr/bin/env python3
"""
O que é: Auditor automatizado de segurança, detecção de vazamento de segredos e fuzzing de guardrails de prompt injection.
Responsabilidade: Varrer artefatos de work items, deltas de memória, handoffs e scripts em busca de credenciais vazadas, injeções maliciosas e comandos de risco.
Pra que serve: Proteger o squad contra vazamentos de dados, execuções de comandos destrutivos e ataques de prompt injection.
Comportamento em falha: Retorna lista detalhada de vulnerabilidades encontradas e encerra com código de saída 1.
Conexões: Utilizado por security-reviewer, offensive-cyber-operator, gates G4 e G6 e suítes de CI/CD.
Dependências & Imports:
  - re, json, pathlib, sys: Operações de expressão regular e inspeção estática de arquivos.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SECRET_PATTERNS = [
    (r"(?i)(api[_-]?key|secret|token|password|passwd|auth[_-]?token)\s*[:=]\s*['\"][A-Za-z0-9_\-./+=]{16,}['\"]", "POTENTIAL_SECRET_EXPOSURE"),
    (r"ghp_[A-Za-z0-9]{36}", "GITHUB_PERSONAL_ACCESS_TOKEN"),
    (r"sk-[A-Za-z0-9]{32,}", "OPENAI_API_KEY"),
    (r"ey[A-Za-z0-9_-]{10,}\.ey[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}", "JWT_TOKEN"),
    (r"-----BEGIN (?:RSA )?PRIVATE KEY-----", "PRIVATE_KEY"),
]

DANGEROUS_SHELL_PATTERNS = [
    (r"rm\s+-rf\s+[\/~]", "DESTRUCTIVE_ROOT_DELETION"),
    (r"(?i)format\s+[A-Za-z]:", "DISK_FORMAT_COMMAND"),
    (r"(?i)chmod\s+-R\s+777", "INSECURE_PERMISSIONS"),
    (r"(?i)curl\s+.*\|\s*(?:bash|sh|pwsh)", "UNVALIDATED_REMOTE_EXECUTION"),
    (r"(?i)eval\s*\(\s*input\s*\(", "DANGEROUS_EVAL_INPUT"),
]

PROMPT_INJECTION_PATTERNS = [
    (r"(?i)ignore\s+(?:all\s+)?(?:previous|prior)\s+(?:instructions|rules|prompts)", "PROMPT_INJECTION_OVERRIDE"),
    (r"(?i)you\s+are\s+now\s+in\s+(?:god|dan|jailbreak)\s+mode", "JAILBREAK_ATTEMPT"),
    (r"(?i)system\s+override\s*:\s*disable\s+(?:security|gates|checks)", "GUARDRAIL_BYPASS_ATTEMPT"),
]


@dataclass
class SecurityFinding:
    """Representa uma vulnerabilidade ou risco de segurança detectado."""
    file_path: str
    line_number: int
    rule_id: str
    severity: str  # 'CRITICAL', 'HIGH', 'MEDIUM', 'LOW'
    description: str
    matched_snippet: str


class SecurityGuardrailAuditor:
    """Scanner de segurança estática para artefatos e código do squad."""

    def scan_text(self, text: str, file_path: str = "<memory>") -> list[SecurityFinding]:
        """Varre uma string de texto em busca de segredos, injeções e comandos perigosos.

        Args:
            text: Conteúdo textual a inspecionar.
            file_path: Caminho de referência do arquivo.

        Returns:
            list[SecurityFinding]: Lista de achados de segurança.
        """
        findings: list[SecurityFinding] = []
        lines = text.splitlines()

        for idx, line in enumerate(lines, 1):
            # 1. Checagem de segredos expostos
            for pattern, rule_id in SECRET_PATTERNS:
                match = re.search(pattern, line)
                if match:
                    findings.append(
                        SecurityFinding(
                            file_path=file_path,
                            line_number=idx,
                            rule_id=rule_id,
                            severity="CRITICAL",
                            description="Possível segredo, token ou credencial exposto em texto aberto.",
                            matched_snippet=line[:80].strip(),
                        )
                    )

            # 2. Checagem de comandos de shell destrutivos
            for pattern, rule_id in DANGEROUS_SHELL_PATTERNS:
                match = re.search(pattern, line)
                if match:
                    findings.append(
                        SecurityFinding(
                            file_path=file_path,
                            line_number=idx,
                            rule_id=rule_id,
                            severity="HIGH",
                            description="Comando de shell destrutivo ou inseguro detectado.",
                            matched_snippet=line[:80].strip(),
                        )
                    )

            # 3. Checagem de prompt injection
            for pattern, rule_id in PROMPT_INJECTION_PATTERNS:
                match = re.search(pattern, line)
                if match:
                    findings.append(
                        SecurityFinding(
                            file_path=file_path,
                            line_number=idx,
                            rule_id=rule_id,
                            severity="CRITICAL",
                            description="Tentativa de desativação de regras de sistema ou prompt injection detectada.",
                            matched_snippet=line[:80].strip(),
                        )
                    )

        return findings

    def scan_file(self, file_path: Path | str) -> list[SecurityFinding]:
        """Lê e inspeciona um arquivo específico no disco.

        Args:
            file_path: Caminho do arquivo a inspecionar.

        Returns:
            list[SecurityFinding]: Lista de vulnerabilidades encontradas.
        """
        path = Path(file_path)
        if not path.is_file():
            return []
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
            return self.scan_text(content, str(path).replace("\\", "/"))
        except Exception:
            return []

    def scan_directory(self, root_dir: Path | str, include_tests: bool = False, include_references: bool = False) -> list[SecurityFinding]:
        """Varre recursivamente diretórios de código, work items e deltas de memória.

        Args:
            root_dir: Diretório raiz a inspecionar.
            include_tests: Se True, inclui suítes de teste nos testes de segurança.
            include_references: Se True, inclui documentações de referência e exemplos de ataques.

        Returns:
            list[SecurityFinding]: Lista consolidada de vulnerabilidades.
        """
        root = Path(root_dir)
        findings: list[SecurityFinding] = []
        extensions = {".py", ".yaml", ".json", ".md", ".sh", ".ps1"}

        for p in root.rglob("*"):
            if p.is_file() and p.suffix in extensions:
                parts = set(p.parts)
                if any(part in {".git", ".temp", ".pytest_cache", "venv", "__pycache__", "vendor"} for part in parts):
                    continue
                if not include_tests and ("tests" in parts or "test" in parts or p.name.startswith("test_")):
                    continue
                if not include_references and ("references" in parts or "examples" in parts):
                    continue
                if p.resolve() == Path(__file__).resolve():
                    continue
                findings.extend(self.scan_file(p))

        return findings


def main(argv: list[str] | None = None) -> int:
    """Ponto de entrada CLI para execução da auditoria de segurança e guardrails."""
    parser = argparse.ArgumentParser(description="Auditor de Segurança e Guardrails do Squad")
    parser.add_argument("--target", default=".", help="Diretório ou arquivo a auditar")
    parser.add_argument("--include-tests", action="store_true", help="Incluir arquivos de teste na auditoria")
    parser.add_argument("--include-references", action="store_true", help="Incluir documentação de referência")
    args = parser.parse_args(argv or sys.argv[1:])

    target_path = Path(args.target)
    auditor = SecurityGuardrailAuditor()

    if target_path.is_file():
        findings = auditor.scan_file(target_path)
    else:
        findings = auditor.scan_directory(
            target_path,
            include_tests=args.include_tests,
            include_references=args.include_references,
        )

    if findings:
        print(f"FALHA DE SEGURANÇA: Encontradas {len(findings)} vulnerabilidades:")
        for f in findings:
            print(f"[{f.severity}] {f.rule_id} em {f.file_path}:{f.line_number} -> {f.description} (Trecho: {f.matched_snippet})")
        return 1
    else:
        print("SUCESSO: Nenhuma vulnerabilidade ou segredo detectado.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
