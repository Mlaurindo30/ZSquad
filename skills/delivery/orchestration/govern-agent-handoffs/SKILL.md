---
name: govern-agent-handoffs
description: Validate agent registry entries, handoff contracts, approvals, evidence lineage, role separation, risk escalation and release readiness across a multi-agent SDLC. Use when onboarding an agent, auditing a work item, reviewing a gate, or resolving traceability gaps.
---

# Govern agent handoffs

## Audit procedure

1. Resolve the sender and receiver in `config/agent-registry.yaml`; confirm the requested capability and skill manifest are present.
2. Validate the handoff against `contracts/handoff.schema.json`. Reject missing IDs, outputs, acceptance criteria or evidence.
3. Check that inputs are the approved artifacts for the current state and that no hidden context is being smuggled through prose.
4. Validate the gate decision against `contracts/gate-decision.schema.json`; compare every criterion with fresh evidence.
5. Check separation of author, reviewer and approver for medium/high risk and confirm human approval where required.
6. Check traceability from epic → story → design/ADR → code diff → tests/evals → release record.
7. Verify prompt and skill versions, model/provider, tool calls, token/cost budget and relevant MLflow trace IDs are recorded.
8. Issue `approved`, `conditionally_approved`, `changes_requested` or `blocked` with exact remediation owners and expiry conditions.

## Non-negotiable controls

- No silent fallback from a failed gate to `done`.
- No approval based only on an agent's narrative or a focused test when the gate requires a full suite.
- No broad secrets, PII or production data in prompts, traces or artifacts without an approved redaction policy.
- Every exception has an owner, compensating control, expiry and follow-up work item.

## Resources

- Use [`references/audit-checklist.md`](references/audit-checklist.md) for the audit fields and severity rubric.

