---
name: refine-requirements-stories
description: Convert user needs, discovery notes, incidents, or business rules into traceable epics and INVEST user stories with testable acceptance criteria, non-functional requirements, risks, dependencies and Databricks/MLflow evaluation expectations. Use before product approval or when feedback invalidates a story.
---

# Refine requirements and stories

## Procedure

1. Capture the user, problem, desired outcome, evidence, constraints and explicit non-goals. Distinguish facts from assumptions.
2. Ask the smallest set of questions that can change scope, priority, risk, data classification or acceptance. Record unanswered questions rather than guessing.
3. Map the outcome to an epic and split vertical slices. Keep each story independently valuable, estimable, testable and small enough for one sprint.
4. Write the story using `templates/user-story.md`. Use Given/When/Then criteria, including negative paths and failure recovery.
5. Add security, privacy, reliability, performance, accessibility and observability requirements when relevant.
6. For GenAI work, specify golden examples, judge/scorer dimensions, tool-use expectations, groundedness, latency and cost budgets.
7. Link each story to source evidence, risks, dependencies, ADRs and planned tests. Mark the story `ready`, `needs_input` or `rejected`.
8. Hand off to `product-owner` with a gate G1 decision request; do not design or implement before approval.

## Quality checks

- INVEST: independent, negotiable, valuable, estimable, small, testable.
- Every acceptance criterion has an observable result and an owner.
- No hidden requirement is introduced by a solution preference.
- Dependencies have an owner and a resolution path.
- Out-of-scope items are explicit.

## Output contract

Produce `requirements.md`, `stories/*.md`, `risk-register.md` and a `handoff.yaml`. Include a short “assumptions and unknowns” section and never use “TBD” without an owner and due condition.

