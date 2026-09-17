# Experimental / Orphaned Integration Modules

## Why these modules were moved

These 10 modules reside in `integrations/experimental/` as specialized integrations.
An exhaustive caller audit was performed across `scripts/`, `agents/`, `integrations/`, `config/`, and `docs/`.

| File | Classification | Role / Operational Purpose | Caller & Reference Evidence |
|---|---|---|---|
| `blast_radius_analyzer.py` | `EXPERIMENTAL_ON_DEMAND` | Dependency graph traversal & impact radius calculation before production/schema changes | `config/skills-catalog.yaml`, `agents/06-software-engineer/skills/manifest.yaml`, `agents/37-fullstack-engineer/skills/manifest.yaml`, `scripts/render_agent_prompt.py`, `scripts/bootstrap_pack.py`, `scripts/tests/test_integrations_adapters.py` |
| `code_health_analyzer.py` | `EXPERIMENTAL_ON_DEMAND` | Codebase health scoring (0.0–10.0) based on cyclomatic complexity and contracts | `config/skills-catalog.yaml`, `agents/05-data-ai-architect/skills/manifest.yaml`, `agents/06-software-engineer/skills/manifest.yaml`, `agents/37-fullstack-engineer/skills/manifest.yaml`, `scripts/bootstrap_pack.py`, `scripts/tests/test_assigned_integrations_coverage.py`, `scripts/tests/test_integrations_adapters.py` |
| `contextual_ast_chunker.py` | `EXPERIMENTAL_ON_DEMAND` | Syntactic AST boundary chunking preserving function and class scopes (cAST) | `config/skills-catalog.yaml`, `agents/06-software-engineer/skills/manifest.yaml`, `agents/17-ai-engineer/skills/manifest.yaml`, `docs/07-operacao-e-integracoes.md`, `scripts/tests/test_assigned_integrations_coverage.py`, `scripts/tests/test_integrations_adapters.py` |
| `gitingest.py` | `EXPERIMENTAL_ON_DEMAND` | Token compaction and budget-aware repository context ingestion for LLMs | `config/skills-catalog.yaml`, `agents/00-delivery-orchestrator/skills/manifest.yaml`, `agents/06-software-engineer/skills/manifest.yaml`, `agents/17-ai-engineer/skills/manifest.yaml`, `scripts/tests/test_e2e_orchestration.py`, `scripts/tests/test_integration_engines.py`, `scripts/tests/test_remaining_integrations_coverage.py` |
| `procedural_skill_engine.py` | `EXPERIMENTAL_ON_DEMAND` | Synthesis of `agentskills.io` skills, AST security audit, and strict linter | `config/skills-catalog.yaml`, `agents/27-platform-engineer/skills/manifest.yaml`, `docs/07-operacao-e-integracoes.md`, `scripts/tests/test_e2e_real_integration.py`, `scripts/tests/test_integrations_adapters.py`, `scripts/tests/test_remaining_integrations_coverage.py` |
| `prompt_quality_optimizer.py` | `EXPERIMENTAL_ON_DEMAND` | Quality scoring for 8-block prompts (Role, Ground Truth, Anti-Fabrication) | `config/skills-catalog.yaml`, `agents/17-ai-engineer/skills/manifest.yaml`, `docs/07-operacao-e-integracoes.md`, `scripts/tests/test_integrations_adapters.py`, `scripts/tests/test_remaining_integrations_coverage.py`, `scripts/tests/test_e2e_real_integration.py` |
| `sdlc_role_mapper.py` | `EXPERIMENTAL_ON_DEMAND` | Role taxonomy mapping for IDE extensions (Cursor, Cline, Roo, Copilot) | `config/skills-catalog.yaml`, `agents/00-delivery-orchestrator/skills/manifest.yaml`, `agents/04-solution-architect/skills/manifest.yaml`, `skills/agent-squad-mcp/SKILL.md`, `docs/07-operacao-e-integracoes.md`, `scripts/tests/test_integrations_adapters.py` |
| `toon.py` | `EXPERIMENTAL_ON_DEMAND` | Token-Oriented Object Notation serialization/deserialization for prompt payloads | `config/skills-catalog.yaml`, manifests of `00, 06, 11, 12, 17`, `scripts/tests/test_integration_engines.py`, `scripts/tests/test_remaining_integrations_coverage.py`, `scripts/tests/test_e2e_orchestration.py` |
| `trajectory_refinement_engine.py` | `EXPERIMENTAL_ON_DEMAND` | Step tracing, ErrorCode taxonomy, and `/refine` heuristic extraction | `config/skills-catalog.yaml`, `agents/00-delivery-orchestrator/skills/manifest.yaml`, `agents/17-ai-engineer/skills/manifest.yaml`, `scripts/bootstrap_pack.py`, `docs/07-operacao-e-integracoes.md`, `scripts/tests/test_integrations_adapters.py` |
| `zcode_subagents.py` | `ACTIVE_CALLED` | Subagent profile synchronization, validation, and CLI management for ZCode IDEs | Directly imported by CLI `scripts/install_zcode_subagents.py`; registered in `config/skills-catalog.yaml`; manifests of `00, 27`; `README.md`, `docs/INSTALLATION.md`; tested in `scripts/tests/test_zcode_subagents.py` |

## Date of move

2026-09-02 — Sprint 2-A (nice-to-have fixes).

## Audit report reference

Full details: `docs/audit-reports/2026-09-02-exhaustive-audit.md`
(Section 4.1 — Dead Code: 12 Orphan Modules; Issue B-4).

## How to restore a module if needed

If a module is needed again, move it back to `integrations/` using:

```bash
git mv integrations/experimental/<module>.py integrations/
```

Then verify that at least one non-test file in the repo imports it (see
verification section below).

## How to verify a module is still needed

Before promoting any module out of `experimental/`, confirm it has an active
caller:

```bash
# Replace <module> with the module name (without .py)
grep -rn "import <module>" integrations/ scripts/ agents/ --include="*.py" | grep -v "experimental/"
```

If the grep returns results outside `integrations/experimental/`, the module has
an active caller and can be considered for promotion. If no results are found,
the module remains orphan and should stay in `experimental/`.
