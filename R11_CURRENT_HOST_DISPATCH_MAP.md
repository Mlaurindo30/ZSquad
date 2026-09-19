# R11 — CURRENT HOST DISPATCH MAP & AUDIT REPORT
**Author:** 27-platform-engineer (Kelsey Hightower & Team Topologies — Internal Developer Platform Architect)  
**Milestone:** R11 (HOST-NATIVE SPECIALIST DISPATCH) — STAGE A  
**Date:** 2026-09-18  
**Status:** COMPLETE (READ-ONLY AUDIT & DISPATCH MAPPING)  
**Trust Boundary:** Agent Squad Control Plane / Runtime Host Interfaces  

---

## 1. Executive Summary & Purpose

Milestone **R10** successfully established the canonical session authority (`CanonicalSessionManager`), deterministic preflight verification (`PreflightValidator`), and durable persistence of authoritative `DelegationEnvelope` records in SQLite (`banco/squad.db`) with status `READY_FOR_DISPATCH`. In strict accordance with the non-goals of R10, the delegation service maintained **ZERO Host Dispatch** (`scripts/runtime/delegation/service.py#L5`).

Milestone **R11 (Host-Native Specialist Dispatch)** represents the critical execution seam: ingesting the validated, session-bound `DelegationEnvelope`, evaluating the target host's capability matrix (`HostCapabilities`), triggering the host-native dispatch mechanism (e.g. Antigravity `invoke_subagent`, Codex background processes, Gemini CLI headless, Claude Code CLI), tracking subagent lifecycle, and minting canonical `DispatchReceipt` evidence records into the audit ledger.

This document serves as the **authoritative Stage A audit and dispatch map**. It provides an exhaustive, read-only inspection of every file in the repository that configures, triggers, invokes, or monitors agents, subprocesses, tools, and queues. It identifies all points of improper coupling, detects envelope bypasses, highlights the gap between R10 contracts and host adapters, and establishes the blueprint for Stage B (Architecture) and Stage C (Implementation by `06-software-engineer`).

---

## 2. Exhaustive Host Adapter Audit (`integrations/`)

An audit of all adapter modules in `integrations/` reveals a foundational architectural reality: **all existing host adapters in `integrations/` are exclusively static configuration / registration adapters for MCP servers via STDIO**. None of them implement runtime dispatch or process lifecycle management.

```
┌───────────────────────────────────────────────────────────────────────────────────┐
│ EXISTING HOST ADAPTERS: MCP CONFIGURATION ONLY (ZERO EXECUTION DISPATCH)         │
├───────────────────────┬──────────────────────────────────┬────────────────────────┤
│ Adapter Module        │ Target Configuration File        │ Injected MCP Block     │
├───────────────────────┼──────────────────────────────────┼────────────────────────┤
│ claude_adapter.py     │ %APPDATA%/Claude/...             │ python -m integrations │
│ codex_adapter.py      │ <config>/config.toml             │ .mcp_runner            │
│ copilot_adapter.py    │ .vscode/mcp.json / %APPDATA%/... │ python -m integrations │
│ hermes_adapter.py     │ ~/.hermes/config.yaml            │ .mcp_runner            │
│ kilo_adapter.py       │ kilo.jsonc                       │ python -m integrations │
│ openclaw_adapter.py   │ ~/.openclaw/openclaw.json        │ .mcp_runner            │
│ zcode_adapter.py      │ .zcode/config.json               │ python -m integrations │
│ (Antigravity Reg.)    │ ~/.gemini/config/mcp_config.json │ python -m integrations │
└───────────────────────┴──────────────────────────────────┴────────────────────────┘
```

### 2.1 `integrations/antigravity_adapter.py`
- **File Status:** `NOT FOUND` / `UNVERIFIED` as a standalone file in `integrations/`.
- **Finding:** While referenced in architectural documentation and prompt instructions, `antigravity_adapter.py` does not exist in `integrations/`.
- **Existing Antigravity Registration:** Handled separately by `scripts/register_antigravity_mcp.py` (54 lines), which injects an `agent-squad` MCP server block into `~/.gemini/config/mcp_config.json` targeting `python -m integrations.mcp_runner` with `PYTHONUNBUFFERED=1` and `PYTHONPATH=%SQUAD_RUNTIME%`.
- **Execution Dispatch Status:** `ZERO`. No Python module currently adapts `DelegationEnvelope` into Antigravity's native `invoke_subagent` payload.

### 2.2 `integrations/claude_adapter.py`
- **Path & Size:** `integrations/claude_adapter.py` (90 lines).
- **Core Functions:** `merge_claude_config(config_path, repo_path)` (Line 15), `setup_claude_desktop(repo_path)` (Line 70).
- **Behavior:** Safely and idempotently merges `mcpServers["agent-squad"]` into `%APPDATA%/Claude/claude_desktop_config.json`. Uses atomic file replacement via `tempfile.NamedTemporaryFile` and `os.replace`.
- **Execution Dispatch Status:** `ZERO`. Does not interface with Claude Desktop CLI, Claude Code CLI, or task subagents.

### 2.3 `integrations/codex_adapter.py`
- **Path & Size:** `integrations/codex_adapter.py` (97 lines).
- **Core Functions:** `validate_toml(config_path)` (Line 18), `register_codex_mcp(config_path, runtime_path)` (Line 25), `rollback_codex_mcp(config_path)` (Line 71).
- **Behavior:** Uses `tomllib` and `tomlkit` to idempotently inject `[mcp_servers.agent_squad_mcp]` into Codex's `config.toml`, pointing to `python -u .../integrations/mcp_runner.py` with `AZURE_DEVOPS_MCP_TRANSPORT=azure-devops`.
- **Execution Dispatch Status:** `ZERO`. Does not invoke `spawn_agent`, manage background workers, or consume `DelegationEnvelope`.

### 2.4 `integrations/copilot_adapter.py`
- **Path & Size:** `integrations/copilot_adapter.py` (95 lines).
- **Core Functions:** `merge_copilot_config(config_path, repo_path)` (Line 15), `setup_copilot_desktop(repo_path, is_global)` (Line 70).
- **Behavior:** Idempotently injects MCP configuration into `.vscode/mcp.json` or VS Code global storage (`%APPDATA%/Code/User/globalStorage/github.copilot/mcp.json`).
- **Execution Dispatch Status:** `ZERO`. Pure configuration adapter.

### 2.5 `integrations/hermes_adapter.py`
- **Path & Size:** `integrations/hermes_adapter.py` (65 lines).
- **Core Functions:** `install_hermes_mcp_server(config_path, squad_runtime_path)` (Line 13).
- **Behavior:** Idempotently parses and writes `~/.hermes/config.yaml` using PyYAML and atomic temporary files.
- **Execution Dispatch Status:** `ZERO`. Pure configuration adapter.

### 2.6 `integrations/kilo_adapter.py`
- **Path & Size:** `integrations/kilo_adapter.py` (99 lines).
- **Core Functions:** `configure_kilo_mcp(target_path, python_path)` (Line 16), `_write_atomically(target_path, content)` (Line 85).
- **Behavior:** Configures `kilo.jsonc` (JSON with Comments) using regex insertion to preserve comments, falling back to `json5`.
- **Execution Dispatch Status:** `ZERO`. Pure configuration adapter.

### 2.7 `integrations/openclaw_adapter.py`
- **Path & Size:** `integrations/openclaw_adapter.py` (69 lines).
- **Core Functions:** `configure_openclaw(openclaw_config_path, squad_runtime_path)` (Line 14).
- **Behavior:** Injects `mcp.servers.agent-squad` into `~/.openclaw/openclaw.json` using `json5` and atomic replace.
- **Execution Dispatch Status:** `ZERO`. Pure configuration adapter.

### 2.8 `integrations/zcode_adapter.py`
- **Path & Size:** `integrations/zcode_adapter.py` (67 lines).
- **Core Functions:** `configure_zcode(zcode_config_path, squad_runtime_path)` (Line 14).
- **Behavior:** Injects `mcpServers.agent-squad` into `.zcode/config.json`.
- **Execution Dispatch Status:** `ZERO`. Pure configuration adapter.

---

## 3. Subprocess & Dispatch Mechanisms in `scripts/`

A forensic audit was conducted across all scripts handling execution, dispatch, eventing, and subagent prompt compilation.

### 3.1 `scripts/sdd_dispatch.py` (FileSDDDispatcher)
- **Component Contract:** Persistent two-phase Spec-Driven Development (SDD) dispatch queue (`Line 22: class FileSDDDispatcher`).
- **Data Flow & Lifecycle:**
  1. `enqueue(root, payload)`: Hashes identity fields `("work_id", "stage", "persona", "briefing_sha256")`. Writes request to `root/requests/{request_id}.json` and queue receipt to `root/queue-receipts/{request_id}.json`.
  2. `claim(root, request_id, consumer)`: Writes exclusive consumer lock to `root/claims/{request_id}.json`. Fails if claimed by another consumer.
  3. `ack(root, request_id, consumer, result)`: Requires `result.status == "completed"` and `result.result_ref`. Writes execution receipt to `root/execution-receipts/{request_id}.json` (`Line 73: receipt_id = f"EXEC-{request_id}"`).
- **Trust Boundary:** Local filesystem ACL (`Line 76: "trust_boundary": "local-filesystem-acl"`).
- **Critical Architectural Gaps:**
  - **Decoupled from Domain Contracts:** Does not consume or validate `DelegationEnvelope` or `ActivationPacket`.
  - **Custom Non-Canonical Receipt:** Generates JSON dict receipts that do not conform to `DispatchReceipt` or `ExecutionReceipt` in `scripts/domain/receipts.py`.
  - **No Host Execution:** Enqueues files passively; depends on external out-of-band consumers to poll and claim requests.

### 3.2 `scripts/orchestration_controller.py` (OrchestrationController)
- **Role:** Post-failure recovery controller (`Line 99: class OrchestrationController`).
- **Mechanics:**
  - Receives `FailureEvent` from subagent calls, host dispatches, or gate rejections.
  - Classifies cause via `scripts.error_classifier.classify_error` into `FailoverReason`.
  - Returns `RecoveryDecision` (`Line 32: RecoveryAction`): `RETRY_SAME`, `RETRY_COMPRESSED`, `ROTATE_AGENT`, `ROTATE_PROVIDER`, `ESCALATE`, `BLOCKED`.
  - Enforces anti-loop retry budget: `_MAX_RETRIES_PER_TARGET = 2`.
  - Persists decisions to SQLite table `ops_recovery` (`Line 200: record_event`).
- **Execution Dispatch Status:** Deterministic and network-free. Does not dispatch specialists; solely calculates failover policy when dispatch or execution fails.

### 3.3 `scripts/continuous_trigger_engine.py` (ContinuousTriggerEngine)
- **Role:** Reactive SDLC lifecycle trigger engine with Circuit Breaker (`Line 206`).
- **Mechanics:**
  - Listens to `EVENT_HANDOFF_CREATED` and `EVENT_GATE_EVALUATED`.
  - Evaluates `POInjectionGuard` (validates G1 human approval for medium/high/critical risk) and `AgileCoachSizingGuard` (blocks items with `story_points > 8`).
  - Advances state via `AgentSquad.advance_state(work_item_id)`.
  - Tripping Circuit Breaker (`HALTED_CIRCUIT_BREAKER`) after 2 consecutive transition failures.
- **Dispatch Observation:**
  - In `run_continuous()` (Lines 535, 544, 569, 589), the engine explicitly returns:
    ```python
    "agent_dispatches": []
    ```
  - **Zero Specialist Dispatch:** The engine advances state FSM and records handoffs, but leaves specialist agent dispatch completely uninstantiated.

### 3.4 `scripts/agent_squad.py`
- **Dispatchable Agents Invariant:** Lines 383–395 enforce that the Delivery Orchestrator (`00-delivery-orchestrator`) is marked `dispatchable: false`. Only specialist agents are registered in `self.dispatchable_agent_ids`.
- **SDD Dispatch Integration:**
  - `sdd_run(work_item_id, stage, dispatch=True)` (Line 2174): Validates stage, authorizes agent, compiles briefing, and if `dispatch=True`, calls `self.sdd_dispatcher.enqueue(item_path / "sdd" / "dispatch", dispatch_payload)`.
  - `sdd_dispatch_claim()` (Line 2321) and `sdd_dispatch_ack()` (Line 2335) interface with `FileSDDDispatcher`.
- **Subprocess Calls:** Subprocess execution is strictly limited to Git CLI, Docker checks, and test runners (`pytest`). There is zero subprocess spawning of agents or host CLI dispatchers.

### 3.5 `scripts/render_agent_prompt.py`
- **Role:** Canonical compiler of specialist agent system prompts.
- **CLI Interface:** `python scripts/render_agent_prompt.py --agent <id> [--work-item <path>] [--project-name <name>] [--assigned ...] [--discovered ...] [--auto-select-skills]`.
- **Assembly Pipeline (Lines 540–563):**
  1. `_build_environment_section()`: Working directory, workspace root, UTC ISO-8601 timestamp.
  2. `_build_prompt_section()`: Loads `%SQUAD_RUNTIME%/agents/<id>/PROMPT.md`.
  3. `_build_cognitive_contract_section()`: Anti-hallucination, Chain-of-Thought, Tree-of-Thoughts, Self-Reflection, 8-Block Briefing.
  4. `_build_skills_section()`: Canonical loading of Native skills, Assigned skills, Discovered skills (respecting 7/3 budget).
  5. `_build_work_item_context()`: `status.yaml`, ancestor specifications (Epic, Feature, Story, Acceptance Criteria).
  6. `_build_azure_devops_section()`: DevOps config, ADO account mapping, MCP tool guidance, ADO-first rule.
  7. `_build_hive_mind_section()`: Global Second Brain integration (`D:/Hive-Mind`).
  8. `_build_engines_section()`: Engine awareness (Continuous Trigger, Error Classifier, Auto-Correction).
- **Execution Dispatch Status:** Pure compiler. Emits authoritative rendered prompt string and calculates SHA-256 instruction hash. Does not invoke subagents.

### 3.6 `integrations/resolvers/assignment_resolver.py`
- **Component:** Resolves `get_assignment` and legacy `prepare_delegation` tool calls in the MCP server.
- **Lines 122–177 (`prepare_delegation`):**
  - Accepts `session`, `target_role`, `scope`, `action`.
  - Assembles 8-block briefing text string.
  - Calls `render_agent_prompt()`.
  - Calculates `p_hash = sha256(rendered_prompt.encode("utf-8")).hexdigest()`.
  - Returns `{"hash": p_hash, "briefing": briefing, "rendered_prompt": rendered_prompt}`.
- **Legacy Defect & Envelope Bypass:**
  - In R10, `DelegationService` was implemented in `scripts/runtime/delegation/service.py`, but `assignment_resolver.py` was retained as a legacy facade without returning the full canonical `DelegationEnvelope` or recording the dispatch intent.

---

## 4. Host-Specific Specialist Invocation Models

Each host environment supported by the Agent Squad platform possesses a distinct execution paradigm for subagent invocation, lifecycle management, and communication.

```
┌────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│ HOST-NATIVE INVOCATION MATRIX                                                                          │
├──────────────┬────────────────────────┬─────────────────────────────────────┬──────────────────────────┤
│ Host Runtime │ Primary Dispatch Tool  │ Payload / Invocation Signature      │ Return / Evidence Path   │
├──────────────┼────────────────────────┼─────────────────────────────────────┼──────────────────────────┤
│ Antigravity  │ invoke_subagent        │ Subagents: [{TypeName, Role,        │ send_message (Recipient) │
│              │ (Native Tool)          │   Prompt: <rendered>, Workspace}]   │ + transcript logs        │
├──────────────┼────────────────────────┼─────────────────────────────────────┼──────────────────────────┤
│ Codex        │ CLI spawn / Background │ codex exec / python -m runner       │ Process exit code,       │
│              │ Runner (Process)       │ --instruction <hash|file>           │ JSON stdout / file ack   │
├──────────────┼────────────────────────┼─────────────────────────────────────┼──────────────────────────┤
│ Gemini CLI   │ agy / gemini CLI       │ gemini --prompt-file <path>         │ Process exit code,       │
│              │ (Headless Subprocess)  │ --output-format json                │ output artifact / receipt│
├──────────────┼────────────────────────┼─────────────────────────────────────┼──────────────────────────┤
│ Claude Code  │ claude CLI / Subagent  │ claude -p "<prompt>" --output json  │ CLI JSON output,         │
│ / Desktop    │ (CLI / STDIO Tool)     │ or stdio MCP task dispatch          │ MCP tool return payload  │
└──────────────┴────────────────────────┴─────────────────────────────────────┴──────────────────────────┘
```

### 4.1 Google Antigravity (Antigravity IDE & `agy` CLI)
- **Execution Model:** Host-native agentic pair-programming IDE with native subagent orchestration.
- **Tool Definition (`invoke_subagent`):**
  ```json
  {
    "name": "invoke_subagent",
    "parameters": {
      "Subagents": [
        {
          "TypeName": "self | research",
          "Role": "string (e.g. 06-software-engineer)",
          "Prompt": "string (AUTHORITATIVE COMPILED INSTRUCTION)",
          "Workspace": "inherit | branch | share",
          "Model": "inherit | flash_lite | flash | pro"
        }
      ]
    }
  }
  ```
- **Lifecycle Management:**
  - `manage_subagents(Action="list" | "kill" | "kill_all", ConversationIds=[...])`.
  - `define_subagent(name, description, system_prompt, enable_write_tools, enable_subagent_tools, enable_mcp_tools)`.
- **Communication & Evidence Return:**
  - Subagents run asynchronously in background.
  - Subagent MUST use `send_message(Recipient="<caller_conversation_id>", Message="<evidence>")` to return results to the parent orchestrator.
  - Output outside `send_message` is not seen by the parent caller.
- **Enforcement Rules (`AGENTS.md` / `GEMINI.md`):**
  - Mandatory prompt rendering before dispatch: `select -> render -> obtain compiled prompt -> invoke`.
  - Single persona per subagent.
  - Segregation of Duties: Author cannot review own work (`assert_sod_compliance`).

### 4.2 OpenAI Codex / CLI Runner
- **Execution Model:** Headless CLI execution or background agent process runner.
- **Invocation Signature:**
  - Command: `codex exec --prompt-file <path> --cwd <work_item_dir>` or `python -m integrations.codex_runner`.
  - Host Capabilities: `has_subagent_dispatch: false`, `has_filesystem_write: true`, `has_terminal_execution: true`, `has_mcp_client: true`, `has_background_tasks: true`.
- **Current Codebase State:** `UNVERIFIED` in code. Only configuration registration exists (`integrations/codex_adapter.py`).

### 4.3 Gemini CLI (`agy` Headless)
- **Execution Model:** Non-interactive headless CLI execution.
- **Invocation Signature:**
  - Command: `agy run --agent <agent_id> --prompt-file <path> --output-format json`.
  - Host Capabilities: `has_subagent_dispatch: true`, `has_filesystem_write: true`, `has_terminal_execution: true`, `has_mcp_client: true`, `has_background_tasks: true`.
- **Current Codebase State:** `UNVERIFIED` in code. Only MCP config registration exists (`scripts/register_antigravity_mcp.py`).

### 4.4 Claude Desktop & Claude Code
- **Execution Model:**
  - Claude Desktop: Interactive GUI application consuming MCP tools via STDIO. Does not expose programmatic subagent spawning CLI to third-party tools.
  - Claude Code: Headless/interactive developer CLI (`claude -p "<prompt>"`).
- **Current Codebase State:** `UNVERIFIED` in code. Only desktop config registration exists (`integrations/claude_adapter.py`).

---

## 5. Domain Contracts & R10 State Baseline

The dispatch map must strictly adhere to the domain models established in R1 and R10.

### 5.1 `scripts/domain/delegation.py`
- **`DelegationEnvelope` (Authoritative Payload):**
  - `delegation_id`: Unique delegation identifier (`DEL-...`).
  - `sender_role`: Orchestrator role (typically `00-delivery-orchestrator`).
  - `target_role`: Specialist persona (e.g. `06-software-engineer`).
  - `work_item_id`: Target work item identifier.
  - `scope_summary`: Concise summary of delegated scope.
  - `action_requested`: Specific action requested.
  - `compiled_instruction`: Verbatim compiled prompt from `ActivationPacket`.
  - `instruction_hash`: Authoritative SHA-256 hash of `compiled_instruction`.
- **`HostCapabilities` (Abstract Capability Matrix):**
  - `has_subagent_dispatch: bool`: Host supports native subagents.
  - `has_filesystem_write: bool`: Host allows workspace writes.
  - `has_terminal_execution: bool`: Host allows shell command execution.
  - `has_mcp_client: bool`: Host can connect to MCP servers.
  - `has_background_tasks: bool`: Host supports long-running background tasks.
  - `max_token_context: int`: Maximum context token window.

### 5.2 `scripts/domain/receipts.py`
- **`DispatchReceipt` (Proof of Dispatch):**
  ```python
  @dataclass(frozen=True)
  class DispatchReceipt(BaseReceipt):
      receipt_id: str             # e.g. "RCP-DISP-..."
      receipt_type: str = "DISPATCH"
      work_item_id: str
      agent_id: str               # Dispatched agent (e.g. "06-software-engineer")
      instruction_hash: str       # SHA-256 matching DelegationEnvelope
      evidence_hash: str          # SHA-256 of dispatch metadata / receipt payload
      target_agent_id: str        # Dispatched agent ID
      delegation_id: str          # Linked DelegationEnvelope ID
      dispatched_at: datetime
  ```
- **`assert_sod_compliance(execution_receipt, validator_receipt)`:**
  - Enforces that `execution_receipt.agent_id != validator_receipt.agent_id`. Author cannot validate or review own work.

### 5.3 `scripts/runtime/delegation/service.py` (R10 Baseline)
- **Status in SQLite:** Records envelope in table `delegation_envelopes` with status `READY_FOR_DISPATCH`.
- **The Seam to R11:**
  - R10 terminates at `READY_FOR_DISPATCH`.
  - Milestone R11 takes this persisted envelope and transitions it to `DISPATCHED` upon emitting to the target host adapter.

---

## 6. Architectural Gaps & Coupling Analysis (R10 -> R11)

The audit reveals six primary architectural gaps that must be solved in R11 Stages B and C:

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│ ARCHITECTURAL GAP ANALYSIS: R10 -> R11 TRANSITION                                      │
├────┬─────────────────────────────┬─────────────────────────────────────────────────────┤
│ #  │ Identified Gap              │ Technical Impact & Risk                             │
├────┼─────────────────────────────┼─────────────────────────────────────────────────────┤
│ G1 │ Configuration vs Execution  │ Adapters only configure JSON/YAML; no runtime       │
│    │ Conflation                  │ dispatch logic exists to invoke agents.             │
├────┼─────────────────────────────┼─────────────────────────────────────────────────────┤
│ G2 │ Absence of Host Capability  │ HostCapabilities is defined in domain but never     │
│    │ Negotiation                 │ probed, verified, or matched against dispatch requirements. │
├────┼─────────────────────────────┼─────────────────────────────────────────────────────┤
│ G3 │ Envelope Bypass in Legacy   │ Resolvers (prepare_delegation) return unvalidated   │
│    │ Resolvers                   │ dicts without linking to DelegationEnvelope.        │
├────┼─────────────────────────────┼─────────────────────────────────────────────────────┤
│ G4 │ Missing DispatchReceipt     │ No component mints or records DispatchReceipt upon │
│    │ Ledger Generation           │ dispatching specialists, breaking evidence chain.   │
├────┼─────────────────────────────┼─────────────────────────────────────────────────────┤
│ G5 │ SDD Dispatch Isolation      │ FileSDDDispatcher operates on isolated files with   │
│    │                             │ arbitrary dicts, unaligned with DelegationEnvelope. │
├────┼─────────────────────────────┼─────────────────────────────────────────────────────┤
│ G6 │ Missing Antigravity Adapter │ Documented antigravity_adapter.py does not exist;   │
│    │ File in integrations/       │ only a registration script is present.              │
└────┴─────────────────────────────┴─────────────────────────────────────────────────────┘
```

### Gap 1: Configuration vs. Execution Conflation
The modules in `integrations/` are called `*_adapter.py`, leading developers to assume they handle host execution. In reality, they are exclusively setup/installer scripts for MCP client configuration blocks. R11 requires a distinct `HostDispatchAdapter` hierarchy to perform runtime invocations.

### Gap 2: Absence of Host Capability Negotiation
`HostCapabilities` is declared in `scripts/domain/delegation.py`, but there is zero logic in the runtime that probes the active host (e.g. checking whether Antigravity tools or CLI tools are available) and prevents dispatch to an incapable host. If a host lacks `has_subagent_dispatch`, the orchestrator must know whether to fallback to background CLI execution or block.

### Gap 3: Envelope Bypass in Legacy Resolvers
The MCP tool `prepare_delegation` in `assignment_resolver.py` constructs a synthetic 8-block briefing and calls `render_agent_prompt()`, but returns an ad-hoc dictionary. It bypasses `DelegationService.prepare_delegation()`, leaving no audit record in `banco/squad.db`.

### Gap 4: Missing DispatchReceipt Ledger Generation
When a dispatch occurs, a canonical `DispatchReceipt` must be instantiated, cryptographically bound to `DelegationEnvelope.instruction_hash`, and saved into the SQLite database. Currently, zero dispatch receipts are generated.

### Gap 5: SDD Dispatch Isolation
`FileSDDDispatcher` in `scripts/sdd_dispatch.py` operates as an independent filesystem queue with custom JSON receipts (`EXEC-SDD-...`). It does not validate `DelegationEnvelope` or produce domain-compliant receipts.

### Gap 6: Missing Antigravity Adapter File
`integrations/antigravity_adapter.py` is absent from disk. Only `scripts/register_antigravity_mcp.py` exists. A true Antigravity dispatch adapter must be implemented to format and trigger `invoke_subagent`.

---

## 7. Target Architecture Blueprint for R11 Stages B & C

Based on the findings of this audit, the target architecture for R11 must introduce an extensible, decoupled Host Dispatch Subsystem in `scripts/runtime/dispatch/`.

```
                  ┌─────────────────────────────────────┐
                  │          R10 Service Layer          │
                  │ DelegationEnvelope (READY_TO_DISP)  │
                  └──────────────────┬──────────────────┘
                                     │ Ingests Envelope
                                     ▼
                  ┌─────────────────────────────────────┐
                  │    Canonical HostDispatchEngine     │
                  │  - Validates Session & Envelope     │
                  │  - Probes & Matches HostCapability  │
                  │  - Selects Target Host Adapter      │
                  └──────────────────┬──────────────────┘
                                     │ Dispatches
                                     ▼
        ┌────────────────────────────┼────────────────────────────┐
        │                            │                            │
        ▼                            ▼                            ▼
┌───────────────┐            ┌───────────────┐            ┌───────────────┐
│  Antigravity  │            │     Codex     │            │  Gemini CLI / │
│    Adapter    │            │    Adapter    │            │  Claude CLI   │
└───────┬───────┘            └───────┬───────┘            └───────┬───────┘
        │                            │                            │
        │ invoke_subagent            │ spawn / background proc    │ CLI subprocess
        ▼                            ▼                            ▼
   Host Runtime                 Host Runtime                 Host Runtime
        │                            │                            │
        └────────────────────────────┼────────────────────────────┘
                                     │ Emits Dispatch Fact
                                     ▼
                  ┌─────────────────────────────────────┐
                  │       DispatchReceipt Minting       │
                  │  - Bound to instruction_hash        │
                  │  - Stored in squad.db audit ledger  │
                  │  - State -> DISPATCHED              │
                  └─────────────────────────────────────┘
```

### 7.1 Required Interfaces for Stage B
1. **`BaseHostDispatchAdapter` (Abstract Interface):**
   - `get_capabilities() -> HostCapabilities`: Declares host capabilities.
   - `can_dispatch(envelope: DelegationEnvelope) -> bool`: Validates compatibility.
   - `dispatch(envelope: DelegationEnvelope, session_id: str) -> DispatchReceipt`: Executes native dispatch.
   - `check_status(receipt_id: str) -> str`: Checks lifecycle state of dispatched agent.
2. **`HostDispatchRegistry`:**
   - Central registry mapping host identifiers (`antigravity`, `codex`, `gemini_cli`, `claude_code`) to concrete adapter implementations.
3. **`HostDispatchEngine`:**
   - Ingests `DelegationEnvelope` with status `READY_FOR_DISPATCH`.
   - Verifies preflight decision and session validity.
   - Selects compatible adapter based on active host and capabilities.
   - Executes dispatch and mints canonical `DispatchReceipt`.
   - Transitions SQLite delegation record to `DISPATCHED`.

---

## 8. Concrete Evidence & File Traceability Matrix

| File Path | Lines | Component / Function | Current Behavior | R11 Architectural Action |
| :--- | :--- | :--- | :--- | :--- |
| `scripts/domain/delegation.py` | 122–177 | `DelegationEnvelope` | Authoritative payload model with `instruction_hash` | Target input contract for R11 dispatch engine |
| `scripts/domain/delegation.py` | 180–194 | `HostCapabilities` | Capability matrix model | Must be implemented and reported by each host adapter |
| `scripts/domain/receipts.py` | 55–69 | `DispatchReceipt` | Domain model proving dispatch | Must be instantiated and persisted upon every dispatch |
| `scripts/runtime/delegation/service.py` | 134–157 | `DelegationService.prepare_delegation` | Sets status `READY_FOR_DISPATCH`; ZERO dispatch | Seam where R11 dispatch engine hooks into workflow |
| `scripts/render_agent_prompt.py` | 540–571 | `render_agent_prompt` | Compiles PROMPT.md, skills, context, cognitive contract | Output is injected verbatim into `invoke_subagent` |
| `integrations/claude_adapter.py` | 15–69 | `merge_claude_config` | Merges JSON config for Claude Desktop | Static config only; R11 requires CLI dispatch adapter |
| `integrations/codex_adapter.py` | 25–70 | `register_codex_mcp` | Injects TOML table into `config.toml` | Static config only; R11 requires CLI/process dispatch adapter |
| `integrations/copilot_adapter.py` | 15–69 | `merge_copilot_config` | Merges JSON config for VS Code Copilot | Static config only; no dispatch capability |
| `integrations/hermes_adapter.py` | 13–64 | `install_hermes_mcp_server` | Merges YAML config into `config.yaml` | Static config only; no dispatch capability |
| `integrations/kilo_adapter.py` | 16–84 | `configure_kilo_mcp` | Modifies `kilo.jsonc` | Static config only; no dispatch capability |
| `integrations/openclaw_adapter.py` | 14–66 | `configure_openclaw` | Modifies `openclaw.json` | Static config only; no dispatch capability |
| `integrations/zcode_adapter.py` | 14–64 | `configure_zcode` | Modifies `config.json` | Static config only; no dispatch capability |
| `scripts/register_antigravity_mcp.py` | 8–51 | `main` | Merges JSON config for Gemini/Antigravity | Static config only; missing `antigravity_adapter.py` |
| `scripts/sdd_dispatch.py` | 22–84 | `FileSDDDispatcher` | Filesystem queue (`requests/`, `claims/`, `receipts/`) | Must be aligned with or superseded by `DelegationEnvelope` |
| `scripts/continuous_trigger_engine.py` | 452–591 | `run_continuous` | Advances lifecycle; returns `agent_dispatches: []` | Must be integrated with R11 dispatch engine |
| `scripts/orchestration_controller.py` | 99–193 | `OrchestrationController.decide` | Classifies failures; selects failover action | Manages failover when host dispatch fails |
| `integrations/resolvers/assignment_resolver.py` | 122–177 | `prepare_delegation` | Assembles legacy briefing and hash | Must be upgraded to invoke canonical `DelegationService` |

---

## 9. Stage A Conclusion & Handoff

Stage A (Read-Only Audit and Current Host Dispatch Mapping) is **COMPLETE**.

### Summary of Audit Findings:
1. **All 8 existing host adapters are exclusively static MCP configuration injectors.** Zero runtime dispatch or subagent execution code exists in `integrations/`.
2. **`antigravity_adapter.py` is missing from `integrations/`** (only `scripts/register_antigravity_mcp.py` exists).
3. **Subprocess execution in `scripts/` is confined to Git, Docker, and Pytest.** No subprocess agent spawning exists.
4. **`FileSDDDispatcher` is isolated from domain contracts** and operates with raw JSON dicts rather than canonical `DelegationEnvelope` and `DispatchReceipt` models.
5. **R10 contracts are ready and waiting:** `DelegationEnvelope` records are persisted in SQLite with status `READY_FOR_DISPATCH`. The execution seam is cleanly decoupled.

### Next Action:
Issue handoff to the Delivery Orchestrator (`00`) to initiate **Stage B (Architecture Specification)** for Milestone R11.
