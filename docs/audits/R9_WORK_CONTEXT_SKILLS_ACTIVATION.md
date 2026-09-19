# R9 & R9.1 — WORK CONTEXT, CANONICAL SKILL RESOLUTION & SPECIALIST ACTIVATION
## Formal Governance, Code Review and Verification Audit

**Status:** APPROVED (R9.1 Determinism & Compiler Authority Verified)  
**Reviewer:** 09-code-reviewer  
**Collaborators:** 27-platform-engineer, 04-solution-architect, 06-software-engineer, 11-test-engineer, 14-governance-auditor  
**Authority:** Canonical Agent Squad Control Plane  

---

## 1. Executive Summary & Verification Matrix

Milestone **R9** and verification **R9.1** deliver the canonical activation engine that transforms an R8 `ExecutionAssignment` into an auditable, immutable `ActivationPacket` containing complete hierarchical ancestor context, canonical skill resolution under strict cognitive budgeting (<= 7 skills), and authoritative compiled instructions.

| Checkpoint | Requirement | Evidence / Implementation | Verdict |
|---|---|---|---|
| **Hierarchical WorkContext** | 4-tier traversal (`TASK` $\rightarrow$ `STORY` $\rightarrow$ `FEATURE` $\rightarrow$ `EPIC`) with specs | `WorkContextBuilder` + `HierarchyContextResolver` | **PASS** |
| **Ancestor Spec Extraction** | Collects `epic.md`, `feature.md`, `story.md` into artifacts | Implemented in `WorkContextBuilder._extract_spec_summary` | **PASS** |
| **Content-Canonical Fingerprinting** | Fingerprint hashes semantic content, ZERO volatile `mtime` | `WorkContextBuilder.compute_fingerprint` (100% semantic) | **PASS** |
| **Mtime-Only Stability** | Files modified only in mtime retain identical fingerprint | Verified in `test_context_fingerprint_stable_across_mtime_modifications` | **PASS** |
| **Ancestor Content Invalidation** | Actual spec content modification invalidates fingerprint | Verified in `test_context_fingerprint_invalidates_on_ancestor_change` | **PASS** |
| **Tree Copy Invariance** | Copied project tree with same content yields same fingerprint | Verified in `test_context_fingerprint_identical_across_copied_project_tree` | **PASS** |
| **Skill Loading Order** | Native $\rightarrow$ Explicit Assigned $\rightarrow$ Discovered | `SkillResolver.resolve_skills` | **PASS** |
| **Canonical Skill Budget** | Budget read from canonical config (`skills-catalog.yaml`) | `SkillResolver.budget = load_canonical_budget()` | **PASS** |
| **Mandatory Skill Overflow** | Native + assigned overflow blocks, never truncates | Verified in `test_mandatory_skill_overflow_blocks_never_truncates` | **PASS** |
| **Control Plane Tool Segregation** | Control plane MCP tools segregated from domain skill budget | Tools declared separately in prompt / requirements | **PASS** |
| **Prompt Preservation** | Specialist `PROMPT.md` preserved without reduction/summary | Verified with sentinel blocks in `test_full_prompt_preservation_with_sentinel_blocks` | **PASS** |
| **Compiler Source Authority** | Zero unauthorized synthetic instruction sections | Verified in `test_zero_unauthorized_compiler_sections` (count = 0) | **PASS** |
| **Single Compiler Authority** | Primitives harmonized, no duplicate compilation engines | `SpecialistInstructionCompiler` reuses canonical renderer primitives | **PASS** |
| **Instruction Hash Integrity** | Hash computed from compiled prompt, not detached briefing | `hashlib.sha256(compiled_instruction.encode("utf-8"))` | **PASS** |
| **Fail-Closed Compilation** | Missing prompt or invalid skill raises typed error | `CompilationError`, `SkillNotFoundError`, `IncompleteContextError` | **PASS** |
| **Activation Persistence** | Idempotent storage in SQLite `activation_packets` table | `ActivationRepository.save` with `UNIQUE` constraint | **PASS** |
| **Activation Service** | Orchestrates assignment $\rightarrow$ ActivationPacket | `ActivationService.activate` | **PASS** |
| **prepare_delegation Boundary** | Propagates `CompilationError`, no R10 scope leak | Verified in `test_prepare_delegation_propagates_compiler_error` | **PASS** |

---

## 2. Inviolable Scope Boundaries (Zero Leakage)

- **ZERO MCP Session Authority:** `SessionStore` and session creation untouched. (Deferred to R10)
- **ZERO DelegationEnvelope Construction:** `DelegationEnvelope` remains decoupled. (Deferred to R10)
- **ZERO MCP Preflight:** Preflight execution validation untouched. (Deferred to R10)
- **ZERO Host Dispatch:** No subagent spawning (`invoke_subagent`) or host process calls. (Deferred to R11)
- **ZERO Specialist Execution / LLM Calls:** Zero generative model inference invoked.
- **ZERO Lifecycle State Mutation:** Work item stages and policies read-only.
- **ZERO Azure DevOps Mutation:** Azure Boards, Repos, and Pipelines untouched.

---

## 3. Diagnostic Baseline Alignment

Execution of `scripts/tests/diagnostics/r0_delegation_contract_red.py`:

| Test ID | Contract Description | Pre-R9 State | Post-R9 State | Status |
|---|---|---|---|---|
| **test_r0_del_001** | Invalid session must fail closed | FAILED | FAILED | **EXPECTED RED (Owned by R10)** |
| **test_r0_del_002** | Preflight must validate real conditions | FAILED | FAILED | **EXPECTED RED (Owned by R10)** |
| **test_r0_del_003** | Prepare delegation must not swallow render failure | FAILED | **PASSED** | **RESOLVED BY R9** |
| **test_r0_del_004** | Compiled instruction must be authoritative payload | FAILED | **PASSED** | **RESOLVED BY R9** |
| **test_r0_del_005** | Ambiguous routing must not silently fallback | PASSED | **PASSED** | **RESOLVED BY R8** |
| **test_r0_del_006** | Skill selection budget & semantics (no `skills[:7]`) | FAILED | **PASSED** | **RESOLVED BY R9** |
| **test_r0_del_007** | Compiled context must include ancestor artifacts | FAILED | **PASSED** | **RESOLVED BY R9** |

---

## 4. Test Suite & Regression Verification

- **R9 Dedicated Tests:**
  - `scripts/tests/test_r9_work_context.py`: 5 passed
  - `scripts/tests/test_r9_skills.py`: 5 passed
  - `scripts/tests/test_r9_compiler.py`: 6 passed
  - `scripts/tests/test_r9_activation_persistence.py`: 2 passed
  - **Subtotal:** 18 passed in 1.06s
- **Full Test Suite Regression:**
  - Command: `python -m pytest scripts/tests --ignore=scripts/tests/diagnostics`
  - **Result:** `1688 passed, 6 skipped, 0 failed, 328 warnings in 226.63s`
- **Structure Validation:**
  - `python scripts/validate_structure.py`: `VALID structure agents=41 active_skills=170 schemas=18`
- **Audit Verification:**
  - `python scripts/agent_squad.py audit`: `AUDIT_OK`
- **Git Diff:**
  - `git diff --check`: 0 errors

---

## 5. Segregation of Duties Attestation

- **27-platform-engineer:** Performed audit of context fingerprinting, proven `VOLATILE_METADATA_DEPENDENT` defect, and defined content-canonical specification.
- **04-solution-architect:** Specified the logical content hashing algorithm, canonical budget loading, and single compiler authority architecture.
- **06-software-engineer:** Implemented the content-canonical `compute_fingerprint`, canonical budget loading in `skills.py` and `skills-catalog.yaml`, compiler primitive harmonization, and `prepare_delegation` error propagation.
- **11-test-engineer:** Authored 18 targeted R9 unit tests proving mtime stability, tree copy invariance, sentinel preservation, zero unauthorized synthetic sections, canonical budget loading, and error propagation; verified 0 failures across 1688 regression tests.
- **09-code-reviewer:** Audited boundary enforcement, confirmed zero scope leakage into R10, verified single compiler authority, and issued formal approval.

---

## 6. Review Verdict

**R9_1_CODE_REVIEW = APPROVED**  
**R9_STATUS_RECHECK = APPROVED**  
**R10_ELIGIBILITY = READY_PENDING_AUTHORIZATION**
