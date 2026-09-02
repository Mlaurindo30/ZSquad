---
name: orchestrate-sdlc-gates
description: Coordinate an agentic software delivery item through the Scrum-Kanban SDLC states, dispatch only the necessary specialists, enforce typed handoffs and gate evidence, and route feedback to the root-cause owner. Use for starting, resuming, reviewing, blocking, or releasing any work item in this repository.
---

# Orchestrate SDLC gates

Use this skill as the supervisor loop. The supervisor owns flow state and evidence completeness; specialists own domain decisions and implementation. Never silently repair a failed gate.

## Workflow

1. Read `config/workflow.yaml`, `config/agent-registry.yaml`, the current work-item artifact and the latest handoff.
2. Classify risk (`low`, `medium`, `high`, `critical`), class of service and domain. Reduce the active agent set to the smallest set that can satisfy the acceptance criteria.
3. Create a checkpoint with state, owner, next gate, open questions, WIP count and a budget/timeout.
4. Dispatch a specialist with a typed handoff based on `contracts/handoff.schema.json`. Include file paths and acceptance criteria, not a large narrative dump.
5. Validate the returned artifact against the gate criteria and `contracts/gate-decision.schema.json`. A claim without a command, test, diff, trace, review or human decision is not evidence.
6. On `approved`, append the decision and advance one state. On `changes_requested`, route to the `root_cause_owner`, preserve the failed evidence and increment the iteration counter.
7. Stop after three unsuccessful iterations of the same gate. Mark `blocked`, summarize the missing authority or evidence and ask for a human decision.
8. On `done`, persist a concise outcome, links to artifacts, cost/latency summary and follow-up items.

## Guardrails

- Set `max_iterations`, `timeout`, `max_tokens` and a cost budget for every loop.
- Surface tool errors to the specialist; do not replace an error with an empty result.
- Keep no more than 5–10 tools visible to one agent; use a registry or lazy loading for larger catalogs.
- Keep working memory limited to the current work item; store durable decisions in the artifact and trace.
- Separate author, reviewer and approver for medium/high-risk work.
- Require human approval for product value, data access, production release, exceptions and security waivers.

## Required supervisor output

```yaml
state: <workflow state>
owner: <agent id>
next_action: <single action>
gate: <gate id or null>
evidence: [<paths, commands, trace ids, decisions>]
risks: [<risk ids>]
blocked: false
```

