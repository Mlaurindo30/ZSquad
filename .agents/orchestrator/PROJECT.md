# Project: Agent Squad Technical Audit & Remediation

## Architecture
- Multi-agent SDLC orchestration platform for Azure DevOps (41 specialist personas).
- Automated state machine in `scripts/agent_squad.py` driven by `config/cycles.yaml` and `config/workflow.yaml`.
- Gate-blocking logic (G1–G6) requiring approved decisions in `gate-decisions/`.
- Story sizing enforcement (Fibonacci 1-8, >8 points delegated to `40-agile-coach`).
- Golden Paths: `new-project`, `user-story`, `bugfix`.
- Multi-Project Azure DevOps setup & integration: dedicated team per product, Area Path `Arthemis\<product>`, Board Rows REST API, Comment Resolution policy, WIQL `[System.AreaPath] UNDER`.

## Feature Inventory
| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| 1 | R1: Flow Integrity Audit | Verify advance-state CLI, cycles.yaml state machine, G1-G6 gate blocking, check-sizing Fibonacci rules, Golden Paths | M1 | ORIGINAL_REQUEST |
| 2 | R2: File & Parameter Consistency | Check templates/devops.yaml, azure_devops_project_setup.py, platform connector/mcp client WIQL, gate_validators.py, agent prompts, AGENTS.md, GEMINI.md, schema | M1/M2 | ORIGINAL_REQUEST |
| 3 | R3: Test Coverage & Execution | Validate validate_structure.py (41 agents), pytest test suite (>=54 tests), state machine & sizing tests, setup tests | M2/M3 | ORIGINAL_REQUEST |
| 4 | Remediation & Fixes | Fix any discrepancies discovered during survey across R1, R2, R3 | M2 | Discovered |
| 5 | Verification & Audit | Ensure structural integrity and test execution pass cleanly; Forensic audit | M3 | System Gate |

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| M1 | Survey & Technical Audit | Comprehensive inspection of R1, R2, R3 criteria against current code | none | IN_PROGRESS |
| M2 | Remediation | Fix any failing acceptance criteria or parameter discrepancies | M1 | PLANNED |
| M3 | Final Validation & Audit | Run all suites, verify structure, execute forensic audit, finalize handoff | M2 | PLANNED |

## Interface Contracts
- CLI: `python scripts/agent_squad.py advance-state --work-item <ID>` raises `SquadError` when gate decisions missing.
- CLI: `python scripts/agent_squad.py check-sizing --points 13` blocks with `40-agile-coach` and `Story Split`.
- DevOps REST API: `PUT https://dev.azure.com/{org}/{proj}/{team}/_apis/work/boards/Stories/rows?api-version=7.1`
- WIQL: `AND [System.AreaPath] UNDER 'Arthemis\<product>'`
