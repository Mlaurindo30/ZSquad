# Agent Squad — Canonical Domain JSON Schemas (Core)

This directory contains the authoritative JSON Schema (Draft 2020-12) specifications for the Agent Squad canonical domain contracts, established under Milestone **R1 — CANONICAL DOMAIN CONTRACTS** (`docs/architecture/R1_CANONICAL_DOMAIN_CONTRACTS.md`).

## Canonical Schemas

| Schema File | Domain Model | Purpose |
|---|---|---|
| `project-binding.schema.json` | `ProjectBinding` & `AdoBinding` | Repository binding and decoupled enterprise delivery backend configuration |
| `work-item.schema.json` | `WorkItem` & `AcceptanceCriterion` | Canonical 4-tier work item entity, Gherkin criteria, and DoD |
| `backlog-plan.schema.json` | `BacklogPlan` & `BacklogPlanItem` | Pre-materialization planning model and sizing verification |
| `lifecycle.schema.json` | `LifecycleStage`, `StagePolicy`, `GateDecision`, `Handoff` | 13-stage canonical lifecycle, gate evaluation and handoff state machine |
| `domain-event.schema.json` | `DomainEvent`, `TriggerPolicy`, `WatchdogFinding` | Event-driven architecture, transactional outbox and scheduler findings |
| `delegation-envelope.schema.json` | `DelegationEnvelope`, `ActivationPacket`, `WorkContext` | Subagent dispatch payload, authoritative instruction hashes and context |
| `receipt.schema.json` | `BaseReceipt` & Specialized Receipts | Cryptographic evidence receipts and Segregation of Duties (SoD) |
| `sync-state.schema.json` | `SyncState`, `AdoWorkItemBinding`, `ReconciliationDecision` | Bidirectional ALM synchronization and drift reconciliation |
| `host-capabilities.schema.json` | `HostCapabilities` | Capability matrix decoupling execution logic from client runtime names |

## Strict Invariants
1. **Draft 2020-12:** All schemas use `$schema: "https://json-schema.org/draft/2020-12/schema"`.
2. **Total Parity:** Field names, enums, required properties, and types correspond 1:1 with `scripts/domain/`.
3. **Zero Secrets in Plain Text:** `AdoBinding` enforces `service_hook_secret_ref` instead of raw secrets (SEC-R1-01).
4. **Zero Provider Coupling:** Models abstract host platforms and AI model providers into capability flags.
