---
name: operate-scrum-kanban
description: Run a hybrid Scrum-Kanban delivery system with sprint goals, refinement, Definition of Ready/Done, WIP limits, classes of service, flow metrics, blocker escalation and evidence-based retrospectives. Use for planning, daily flow, reviews, retrospectives or process health.
---

# Operate Scrum with Kanban

Use Scrum for cadence and product feedback; use Kanban inside the sprint for pull-based execution. The sprint goal is fixed, but the order of ready work can change when the product owner accepts the trade-off.

## Cadence

1. Refine weekly: split stories, validate DoR, estimate uncertainty and identify dependencies.
2. Plan every 14 days: choose a sprint goal, set capacity and select ready work by value/risk.
3. Review flow daily: inspect blocked items, WIP limits, aging work and feedback queues; do not start work while a downstream limit is full.
4. Review at sprint end: demonstrate accepted outcomes and inspect rejected/unfinished work without hiding it.
5. Retrospect at sprint end: use cycle time, throughput, escaped defects, rework rate, gate retries and agent cost/latency to choose one or two experiments.

## Policies

- Pull only from `ready`; an expedite item requires explicit human approval and consumes the single expedite slot.
- WIP limits are policies, not targets. Swarm around aging or blocked work before pulling new work.
- Rework returns to the cause owner and keeps the same work-item ID; do not create duplicate stories for feedback.
- Track `lead_time`, `cycle_time`, `time_in_state`, `blocked_time`, `throughput`, `rework_rate`, `defect_escape_rate` and `gate_pass_rate`.
- Forecast with ranges and confidence; never promise a date from velocity alone.

## Required artifacts

Maintain `sprint-goal.md`, `board.yaml`, `flow-metrics.json`, `review-notes.md` and `retro.md`. Link every metric to the source events and state the observation window.

