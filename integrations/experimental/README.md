# Experimental / Orphaned Integration Modules

## Why these modules were moved

These 12 modules were moved from `integrations/` to `integrations/experimental/`
during the **2026-09-02 exhaustive audit**
(`docs/audit-reports/2026-09-02-exhaustive-audit.md`, section 4.1).

The audit found that **no active callers exist** for any of these modules — they
are dead code with zero imports across the entire repository. The active
integration connector (`devops_platform_connector.py`) remains in
`integrations/` and is the sole actively-used integration module.

| File | Audit Status |
|---|---|
| `blast_radius_analyzer.py` | No caller |
| `clone_or_update_repos.py` | No caller |
| `code_health_analyzer.py` | No caller |
| `codebase_knowledge_graph.py` | No caller |
| `contextual_ast_chunker.py` | No caller |
| `gitingest.py` | No caller |
| `procedural_skill_engine.py` | No caller |
| `prompt_quality_optimizer.py` | No caller |
| `sdlc_role_mapper.py` | No caller |
| `toon.py` | No caller |
| `trajectory_refinement_engine.py` | No caller |
| `zcode_subagents.py` | No caller |

## Date of move

2026-09-02 — Sprint 2-A (nice-to-have fixes).

## Audit report reference

Full details: `docs/audit-reports/2026-09-02-exhaustive-audit.md`
(Section 4.1 — Dead Code: 12 Orphan Modules; Issue B-4).

## How to restore a module if needed

If a module is needed again, move it back to `integrations/` using:

```bash
git mv integrations/experimental/<module>.py integrations/
```

Then verify that at least one non-test file in the repo imports it (see
verification section below).

## How to verify a module is still needed

Before promoting any module out of `experimental/`, confirm it has an active
caller:

```bash
# Replace <module> with the module name (without .py)
grep -rn "import <module>" integrations/ scripts/ agents/ --include="*.py" | grep -v "experimental/"
```

If the grep returns results outside `integrations/experimental/`, the module has
an active caller and can be considered for promotion. If no results are found,
the module remains orphan and should stay in `experimental/`.
