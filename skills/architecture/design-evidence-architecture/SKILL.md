---
name: design-evidence-architecture
description: Create solution designs that compare options, record ADRs, model data and failure paths, define security threats, test strategy, observability, rollback and operational evidence. Use for architecture/design gates, Databricks or MLflow workloads, high-risk changes, or when review feedback challenges feasibility.
---

# Design architecture with evidence

## Procedure

1. Read approved stories, constraints, repository instructions, domain skills and existing ADRs. Retrieve only relevant references.
2. Define context, actors, trust boundaries, data classification, lifecycle, dependencies and quality attributes.
3. Compare at least two viable options. Record decision drivers, trade-offs, rejected alternatives and evidence in `templates/adr.md`.
4. Draw component, sequence, state, data-flow and failure-path diagrams. Mark synchronous versus asynchronous communication and retry/idempotency behavior.
5. For Databricks, decide Unity Catalog boundaries, Delta/Lakehouse/Lakebase usage, compute, Vector Search, serving, DABs and deployment environment. For GenAI, define model, tools, retrieval, prompt/version and evaluation interfaces.
6. Run a STRIDE/threat-model pass and define mitigations, residual risk, secrets and authorization boundaries before implementation.
7. Define the test pyramid, contract tests, golden dataset, non-functional thresholds, MLflow traces/scorers, dashboards and rollback signals.
8. Produce a design package, request G2 review from product, security and governance, and route changes to the affected decision owner.

## Evidence standard

Each important claim must point to a source file, command, prototype, benchmark, trace, test or explicit human decision. Label unknowns as hypotheses and define how they will be falsified.

## Resources

- Use [`references/design-review-checklist.md`](references/design-review-checklist.md) for the mandatory design checklist.

