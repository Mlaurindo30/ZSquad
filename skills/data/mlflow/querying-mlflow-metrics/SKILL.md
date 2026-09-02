---
name: querying-mlflow-metrics
description: Fetches aggregated trace metrics (token usage, latency, trace counts, quality evaluations) from MLflow tracking servers. Use when asked to show metrics, analyze token usage, view LLM costs, check usage trends, or query trace statistics.
license: Apache-2.0
---

# Querying MLflow Metrics

## Purpose

Read aggregated metrics from an MLflow tracking server: trace counts, token usage,
latency percentiles and evaluation outcomes. Never write back to MLflow from this
skill.

## When to Use

- The user asks for token usage, latency, cost or quality over a time window.
- A dashboard, table or report needs trace-level statistics.
- A previous run collected traces and the user wants them summarized.

## When NOT to Use

- Logging experiments, registering models or promoting runs — see other MLflow skills.
- Production observability — use `sre-observability-engineer` instead.

## Inputs

- `tracking_uri` (required): MLflow tracking server URL, from `MLFLOW_TRACKING_URI`.
- `experiment_name` (optional): filter by experiment; otherwise all experiments.
- `window` (optional): ISO-8601 duration like `P7D`; default `P7D`.
- `metric_keys` (optional): subset of metrics to return; default all known.

## Outputs

JSON object with `window`, `experiment_name`, `metrics` (dict), `generated_at`.

## Method

1. Resolve `tracking_uri` from env; raise if missing.
2. Open an `mlflow.MlflowClient` with that URI.
3. Call `search_traces` filtered by `experiment_name` and `window`.
4. Group by trace status, compute token totals, latency percentiles.
5. Return the JSON result; do not log or mutate.

## Failure Modes

- `MlflowException` → report tracking server URL and exit non-zero.
- Empty result set → return the structure with empty `metrics`, not an error.
- Network timeout → surface the cause verbatim.

## Safety

Read-only by design. Never delete runs, never modify tags. Never log credentials.

## Anti-fabrication

If `tracking_uri` is not configured, report `NOT CONFIGURED` rather than guessing.
