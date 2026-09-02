"""18-skill-curator — curadoria e gates de qualidade para skills em quarentena.

Inspirado no fluxo de curadoria do Hermes Agent e no skill-curator do
OpenCode. Antes de promover uma skill de ``skills/discovery/intake/`` para
o catálogo ativo, ela passa por:

1. **Hermes Linter** — estrutura e convenção do SKILL.md
2. **Security Gate** — padrões perigosos, segredos, comandos destrutivos
3. **License Gate** — licença declarada vs. licença aceitável
4. **Anti-Fabrication Gate** — marcadores obrigatórios (ground truth, empty if empty)

Uso:
    from scripts.skill_curator import review_intake_skill, CuratorReport

    report = review_intake_skill(Path("skills/discovery/intake/my-skill"))
    if report.approved:
        # prosseguir com promoção
    else:
        for finding in report.findings:
            print(f"[{finding.severity}] {finding.gate}: {finding.message}")
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional, Sequence


# ---------------------------------------------------------------------------
# Modelos
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CuratorFinding:
    gate: str
    severity: str  # BLOCKER | WARNING | ADVISORY
    message: str
    evidence: Optional[str] = None


@dataclass(frozen=True)
class CuratorReport:
    skill_name: str
    approved: bool
    findings: tuple[CuratorFinding, ...]
    gates_run: tuple[str, ...]


# ---------------------------------------------------------------------------
# Gates individuais
# ---------------------------------------------------------------------------

_DANGEROUS_PATTERNS = [
    (r"rm\s+-rf\s+" + chr(47), "destructive root deletion"),
    (r"curl\s+" + chr(92) + r"|[^>]*\s*bash", "remote script piped to shell"),
    (r"eval\s*\(", "eval() usage"),
    (r"os\.system\s*\(", "os.system() usage"),
    (r"subprocess\.(call|run|Popen)\s*\([^)]*shell\s*=\s*True", "subprocess with shell=True"),
    (r"__import__\s*\(", "dynamic import via __import__"),
    (r"pickle\.loads?\s*\(", "pickle deserialization"),
    (r"yaml\.load\s*\([^)]*Loader\s*=\s*None", "unsafe yaml.load without Loader"),
]

_SECRET_PATTERNS = [
    (r"(api[_-]?key|apikey|secret|token|password|passwd|pwd)\s*[:=]\s*['\"][^'\"]{8,}['\"]", "possible hardcoded secret"),
    (r"sk-[a-zA-Z0-9]{20,}", "possible API key"),
    (r"ghp_[a-zA-Z0-9]{36,}", "possible GitHub token"),
    (r"AKIA[0-9A-Z]{16}", "possible AWS access key"),
]

_LICENSE_KEYWORDS = {
    "mit": ["mit license", "permission is hereby granted"],
    "apache": ["apache license", "version 2.0"],
    "gpl": ["gnu general public license", "gpl v"],
    "bsd": ["bsd license", "redistribution and use"],
    "unlicense": ["unlicense", "public domain"],
}


def _check_dangerous_patterns(content: str) -> List[CuratorFinding]:
    findings = []
    for pattern, desc in _DANGEROUS_PATTERNS:
        if re.search(pattern, content, re.IGNORECASE):
            findings.append(CuratorFinding(
                gate="security-dangerous-patterns",
                severity="BLOCKER",
                message=f"Padrão perigoso detectado: {desc}",
                evidence=pattern,
            ))
    return findings


def _check_secrets(content: str) -> List[CuratorFinding]:
    findings = []
    for pattern, desc in _SECRET_PATTERNS:
        matches = re.findall(pattern, content, re.IGNORECASE)
        if matches:
            findings.append(CuratorFinding(
                gate="security-secrets",
                severity="BLOCKER",
                message=f"Possível segredo hardcoded: {desc}",
                evidence=f"{len(matches)} match(es)",
            ))
    return findings


def _check_license(skill_md: Path) -> List[CuratorFinding]:
    findings = []
    try:
        text = skill_md.read_text(encoding="utf-8").lower()
        declared = None
        for lic, keywords in _LICENSE_KEYWORDS.items():
            if any(kw in text for kw in keywords):
                declared = lic
                break

        if declared is None:
            findings.append(CuratorFinding(
                gate="license-compliance",
                severity="WARNING",
                message="Licença não declarada explicitamente no SKILL.md.",
            ))
        elif declared not in {"mit", "apache", "bsd", "unlicense"}:
            findings.append(CuratorFinding(
                gate="license-compliance",
                severity="ADVISORY",
                message=f"Licença '{declared}' detectada; verificar compatibilidade com internal-use.",
            ))
    except Exception:
        pass
    return findings


def _check_anti_fabrication(content: str) -> List[CuratorFinding]:
    findings = []
    lower = content.lower()
    required_markers = [
        "empty if empty",
        "not found",
        "unverified",
        "ground truth",
        "boundary",
        "anti-fabricação",
        "antifabricação",
    ]
    missing = [m for m in required_markers if m not in lower]
    if missing:
        findings.append(CuratorFinding(
            gate="anti-fabrication",
            severity="WARNING",
            message=f"Marcadores anti-fabricação ausentes: {', '.join(missing)}",
        ))
    return findings


# ---------------------------------------------------------------------------
# Review principal
# ---------------------------------------------------------------------------

def review_intake_skill(
    skill_dir: Path,
    *,
    run_linter: bool = True,
    linter_callable: Optional[Any] = None,
) -> CuratorReport:
    """Executa todos os gates de curadoria sobre uma skill em intake.

    Args:
        skill_dir: diretório da skill em ``skills/discovery/intake/<name>/``.
        run_linter: se ``True``, executa o linter Hermes.
        linter_callable: função opcional ``(markdown_text, expected_slug) -> list[LintFinding]``.
            Se ``None``, usa o linter embutido de ``auto_skill_learner``.

    Returns:
        ``CuratorReport`` com aprovação e achados.
    """
    skill_name = skill_dir.name
    skill_md = skill_dir / "SKILL.md"
    findings: list[CuratorFinding] = []
    gates_run: list[str] = []

    if not skill_md.is_file():
        return CuratorReport(
            skill_name=skill_name,
            approved=False,
            findings=(CuratorFinding(gate="presence", severity="BLOCKER", message="SKILL.md ausente"),),
            gates_run=("presence",),
        )

    content = skill_md.read_text(encoding="utf-8")

    # Gate 1: dangerous patterns
    gates_run.append("security-dangerous-patterns")
    findings.extend(_check_dangerous_patterns(content))

    # Gate 2: secrets
    gates_run.append("security-secrets")
    findings.extend(_check_secrets(content))

    # Gate 3: license
    gates_run.append("license-compliance")
    findings.extend(_check_license(skill_md))

    # Gate 4: anti-fabrication
    gates_run.append("anti-fabrication")
    findings.extend(_check_anti_fabrication(content))

    # Gate 5: Hermes linter
    if run_linter:
        gates_run.append("hermes-linter")
        if linter_callable is not None:
            lint_findings = linter_callable(content, expected_slug=skill_name)
        else:
            try:
                from scripts.auto_skill_learner import AutoSkillLearner
                lint_findings = AutoSkillLearner().lint_skill_markdown(content, expected_slug=skill_name)
            except Exception:
                lint_findings = []
        for lf in lint_findings:
            findings.append(CuratorFinding(
                gate="hermes-linter",
                severity=lf.severity,
                message=lf.message,
                evidence=f"L{lf.line_number}:{lf.rule_id}",
            ))

    blockers = [f for f in findings if f.severity == "BLOCKER"]
    approved = len(blockers) == 0

    return CuratorReport(
        skill_name=skill_name,
        approved=approved,
        findings=tuple(findings),
        gates_run=tuple(gates_run),
    )
