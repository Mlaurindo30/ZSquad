# R8 — STAGE-AWARE SPECIALIST ROUTING ENGINE
## Canonical Architecture Specification (Updated R8.2)

**Status:** APPROVED (R8.2 Routing Authority & Concurrency Closure)  
**Author:** 04-solution-architect  
**Authority:** Canonical Agent Squad Control Plane  

---

## 1. Executive Summary & Core Invariant

The Stage-Aware Specialist Routing Engine provides a deterministic, closed-loop answer to:

> *"Given this project, work item, lifecycle stage, trigger, and required capability, which logical Agent Squad specialist is responsible?"*

It enforces:
- **Zero silent fallback:** Never silently assigns `software-engineer` or defaults on empty/ambiguous matches.
- **Fail-closed semantics:** Zero matching candidates produce `RoutingStatus.BLOCKED`. Unresolvable ambiguity produces `RoutingStatus.NEEDS_ROUTING`.
- **Single Routing Authority:** Both new callers and legacy compatibility adapters (`get_assignment`) route strictly through `SpecialistRouter`.
- **Pure logical routing:** It does not render prompts, does not load skills, does not touch MCP tools, does not invoke subagents, does not call LLMs, and does not mutate lifecycle or Azure DevOps state.

---

## 2. Durable Canonical ID Allocation (R8.2 Closure)

### 2.1 Problem with Process-Local Locks
Locks in Python (`threading.Lock`) or process-level memory structures protect only threads within a single process. Under concurrent execution across multiple CLI invocations, subagents, or worker processes, two processes could choose the same canonical ID before writing to lifecycle state.

### 2.2 Canonical Durable Reservation Pattern
Durable inter-process concurrency is governed directly inside SQLite via an atomic reservation table and `BEGIN IMMEDIATE` transactions:

```sql
CREATE TABLE IF NOT EXISTS canonical_id_reservations (
    reservation_id   TEXT PRIMARY KEY,
    project_id       TEXT NOT NULL,
    kind             TEXT NOT NULL,
    canonical_id     TEXT NOT NULL,
    reserved_at      TEXT NOT NULL,
    UNIQUE(project_id, canonical_id)
);
```

### 2.3 Atomic Allocation & Reservation
Under `BEGIN IMMEDIATE`:
1. Query occupied IDs across SQLite lifecycle state, bindings, backlog plans, filesystem, and `canonical_id_reservations`.
2. Compute the next sequential candidate ID.
3. Atomically `INSERT` the reservation into `canonical_id_reservations`.
4. `COMMIT` transaction and return the reserved canonical ID.

If another process attempts allocation concurrently, SQLite's busy handler waits up to 15 seconds, reads the newly committed reservation, and deterministically allocates the next sequential ID. Multiple OS processes cannot collide.

---

## 3. Capability Architecture: Structured Canonical

Capabilities are explicitly declared in `config/agent-registry.yaml`:

```yaml
- id: software-engineer
  title: Clean Code & TDD Craftsman
  capabilities:
    - tdd
    - clean-code
    - unit-testing
    - integration-testing
    - refactoring
    - solid
```

`AgentRegistry` parses and stores these capabilities as an immutable `frozenset`. Capability matching is performed against this structured set. Free-text purpose keyword extraction is strictly non-authoritative (`extract_diagnostic_keywords`).

Classification: **`R8_CAPABILITY_SOURCE = STRUCTURED_CANONICAL`**.

---

## 4. Role Normalization: Compatibility-Only Boundary

Role aliasing is achieved via an algorithmic prefix strip plus a minimal legacy exception map:

$$\text{Canonical Role Vocabulary} \longrightarrow \text{Algorithmic Prefix Strip} \longrightarrow \text{Legacy Exception Map} \longrightarrow \text{Canonical Registry ID}$$

1. **Algorithmic Strip:** `re.sub(r"^\d{2}-", "", role_ref)` maps `06-software-engineer` $\rightarrow$ `software-engineer`.
2. **Legacy Exception Map:** `LEGACY_ROLE_EXCEPTIONS` bridges historical vocabulary differences:
   - `10-security-specialist` / `security-specialist` $\longrightarrow$ `security-reviewer`

Classification: **`R8_ROLE_ALIAS_MODEL = COMPATIBILITY_ONLY`**.

---

## 5. Legacy Adapter Architecture (`get_assignment`)

`integrations/resolvers/assignment_resolver.py::get_assignment` is a strict compatibility facade over `SpecialistRouter`:
- Translates legacy arguments (`objective_digest`, `target_role`, `stage`) into `RoutingRequest`.
- Invokes `router.route(request)`.
- If `ASSIGNED`: returns `persona_id: selected_agent_id` with minimal compatibility skills shape.
- If `BLOCKED` or `NEEDS_ROUTING`: returns `persona_id: None` with status and reason.
- **Zero independent lexical scoring and zero silent fallback to software-engineer.**
