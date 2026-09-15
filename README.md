# Agents Squad

Governed multi-agent software delivery framework operating through local artifacts, typed handoffs, memory deltas, and verification gates (G1–G6).

## Overview

Agents Squad organizes specialized AI agent personas into a coordinated engineering organization. Each agent operates under strict segregation of duties, using explicit domain frameworks, empirical evidence, and auditable deliverables.

## Core Delivery Flow

```text
Discovery & Problem Framing (G1-product)
         │
         ▼
Architecture & Threat Modeling (G2-design)
         │
         ▼
Readiness & Skill Resolution (G3-readiness)
         │
         ▼
TDD Implementation & Security Review (G4-code-security)
         │
         ▼
Automated & Exploratory QA (G5-quality)
         │
         ▼
Governance & Controlled Release (G6-governance-release)
```

## Governance & Operational Contracts

1. **Artifact-Driven Execution**: State lives in `work/<WORK-ID>/status.yaml`, never in ephemeral conversation memory.
2. **Mandatory Handoffs**: Every task transition requires a schema-validated `HANDOFF-*.yaml` containing verified output, concrete artifact links, and memory deltas.
3. **Evidence-Based Gates**: No gate passes without executed command output, passing test suites, and clean static analysis.
4. **Segregation of Duties (SoD)**: The author of an artifact cannot approve it at risk medium or above.

## Squad Personas

The squad consists of authentic domain expert personas spanning coordination, product, architecture, engineering, security, data/AI, and release governance.

- **00-03**: Coordination, Product & Flow (`delivery-orchestrator`, `requirements-analyst`, `product-owner`, `scrum-master`)
- **04-05, 23**: Architecture & AI Systems (`solution-architect`, `data-ai-architect`, `data-architect`)
- **06-08, 16-17, 21-22, 24-25, 27-29**: Implementation & Engineering (`software-engineer`, `data-engineer`, `mlops-llmops-engineer`, `dba-databricks-engineer`, `ai-engineer`, `frontend-engineer`, `backend-engineer`, `ml-engineer`, `agent-rag-engineer`, `platform-engineer`, `performance-engineer`, `integration-engineer`)
- **09-12, 28**: Review, Quality & Security (`code-reviewer`, `security-reviewer`, `test-engineer`, `qa-engineer`)
- **13-14, 26**: DevOps, Release, SRE & Governance (`devops-release-engineer`, `governance-auditor`, `sre-observability-engineer`)
- **15, 18-20, 30-35**: Analysis, UX, Strategy & Growth (`ai-analyst`, `skill-curator`, `technical-writer`, `ux-ui-designer`, `brand-strategist`, `copywriter`, `growth-marketing-strategist`, `storytelling-strategist`, `cybersecurity-operator`, `swarm-consensus-coordinator`)

## Getting Started

1. **One-Command Zero-to-Hero Installation** (Isolated `.venv` + SQLite + MCPs + Docker):
   ```powershell
   .\install.ps1
   # or
   python scripts/setup_environment.py
   ```
2. Read `AGENTS.md` and `config/workflow.yaml`.
3. Initialize a governed work item:
   ```powershell
   python scripts/agent_squad.py init-work-item --id EPIC-EXAMPLE --risk medium
   ```
4. Auto-Skill Synthesis & Refinement:
   ```powershell
   python scripts/auto_skill_learner.py learn --work-item TASK-001 --name my-skill
   ```
5. Run full verification and audit:
   ```powershell
   python scripts/validate_structure.py
   python scripts/agent_squad.py audit
   python scripts/verify_clean_code.py scripts/*.py integrations/*.py
   python scripts/audit_security_guardrails.py --target scripts
   python -m pytest scripts/tests/ --cov=scripts --cov=integrations --cov-branch
   ```

## Evidência executável e gates fail-closed

Gates de qualidade não aceitam mais alegações textuais de sucesso. As verificações devem ser executadas por `scripts/quality_gate_runner.py`, persistidas em `work/<WORK-ID>/evaluation/<verifier>.json` e validadas contra `contracts/verification-evidence.schema.json`. O gate também recalcula os hashes dos arquivos vinculados; evidência ausente, inválida, adulterada ou associada a outro work item reprova o critério.

G4 exige evidências independentes de Clean Code, testes, segurança e um ciclo TDD encadeado Red → Green → Refactor. G5 exige BDD, regressão e cobertura executados. Os nomes canônicos e responsáveis estão em `config/workflow.yaml`.
6. Optional: Run full Docker Stack:
   ```powershell
   docker compose up -d
   ```

## Project Onboarding

Use this runbook to onboard a fresh project to the Agents Squad runtime and Azure DevOps governance. All commands assume the repository root unless stated otherwise.

### 1. Link a new project to the shared runtime

Bootstrap a target project so it consumes the shared `SQUAD_RUNTIME` instead of copying the runtime into the product repo:

```powershell
python scripts/bootstrap_project_squad.py --runtime <SQUAD_RUNTIME> --target <project_root>
python scripts/bootstrap_project_squad.py --check
```

`--check` validates the link is healthy. The script writes only `config/project.yaml` and `PROVENANCE.yaml` under `<project_root>/.agents_squad/`.

### 2. Configure Azure DevOps identities

Copy the template and adjust organisation, project, repository, and email accounts:

```powershell
Copy-Item templates/devops.yaml .agents_squad/config/devops.yaml
# then edit .agents_squad/config/devops.yaml:
#   devops.organization, devops.project, devops.repository
#   identities.human_master, development_team, pr_and_card_approver
#   service_accounts.cyber_red, customer_data_pii
```

The three principal Azure DevOps accounts (`squads@`, `arthemis@`, plus `human_master`) and the two service accounts are defined in `templates/devops.yaml.identities` / `templates/devops.yaml.service_accounts`. The SoD-compliant approval model is documented in `agents/_shared/OPERATING_CONTRACT.md` §"Quem aprova o quê (US-17, 2026-09-02)".

### 3. Run the project setup

Provision the project structure and work directory on the target machine:

```powershell
python scripts/azure_devops_project_setup.py --apply
```

Use `--dry-run` first to preview the changes. The setup creates the initial iterations, area paths, and the work-item skeleton.

### 4. Create the first work item

Initialize a governed work item with explicit risk and story-point sizing:

```powershell
python scripts/agent_squad.py init-work-item --id US-001 --risk low
```

The command writes `work/US-001/status.yaml` and the linked scaffolding. Stories above 8 Fibonacci points are blocked by the orchestrator and must be split first.

### 5. Validate the structure

Confirm the project layout and contracts are intact:

```powershell
python scripts/validate_structure.py
```

A non-zero exit or a `INVALID` verdict blocks the next gate (G1).

### 6. Run the test suite

Execute the full test suite and coverage report:

```powershell
python -m pytest scripts/tests/
```

Coverage threshold is governed by `pyproject.toml` (`fail_under = 85` until the suite is fully stabilized; long-term target is 100%).

### 7. Delivery history

Every work item produces an immutable ledger entry. Inspect the chronological delivery record at:

- `docs/delivery-ledger.md`

The ledger records work-item IDs, gate decisions (G1–G6), handoffs, and verification evidence.

### 8. Audit history

Periodic and ad-hoc audits land under:

- `docs/audit-reports/`

Each report is stamped with its date and scope (e.g. `2026-09-02-exhaustive-audit.md`). Use these as the canonical record of which findings were open, fixed, or accepted as risk.

