# C4 — Container (Level 2)

> Diagrama C4 nível 2 (Container). Decompoe o sistema em containers
> (deployáveis/rodáveis): scripts Python, schemas JSON, Azure DevOps
> resources, e flows de dados.

```mermaid
C4Container
    title Container — Agents Squad Runtime

    Person(user, "Orchestrator (00)", "Coordena a entrega")

    Container_Boundary(runtime, "Agents Squad Runtime") {
        Container(agents, "agents/ (41 personas)", "Markdown + YAML", "PROMPT.md + skills/manifest.yaml por persona")
        Container(scripts, "scripts/", "Python 3.11+", "agent_squad.py, validate_structure.py, azure_devops_project_setup.py, pr_governance.py, bdd_runner.py")
        Container(contracts, "contracts/", "JSON Schema 2020-12", "13 schemas: work-item, gate-decision, handoff, devops-config, etc.")
        Container(workflow, "config/", "YAML", "workflow.yaml, cycles.yaml, squads.yaml, agent-registry.yaml")
        ContainerDb(db, "banco/squad.db", "SQLite WAL", "Métricas, AST symbols, trajectories, votes, workflow_metrics")
    }

    Container_Boundary(ado, "Azure DevOps") {
        Container(adoRepos, "Repos", "Git", "defaultBranch main; CODEOWNERS path filter")
        Container(adoBoards, "Boards", "Work Items", "7 states; phase_tags; squad_tags; service_accounts")
        Container(adoPipes, "Pipelines", "azure-pipelines.yml", "lint + unit + SAST + build + test")
    }

    Container_Boundary(memory, "Memory") {
        ContainerDb(sinapse, "Sinapse (Hive-Mind)", "UMC + Graphiti + sqlite-vec", "Decisões, learnings, observations")
    }

    Rel(user, scripts, "Invoca subcomandos", "CLI")
    Rel(scripts, contracts, "Valida payloads", "Draft202012Validator")
    Rel(scripts, workflow, "Carrega config", "yaml.safe_load")
    Rel(scripts, db, "Registra métricas", "sqlite3 WAL")
    Rel(scripts, ado, "REST API", "service connections (3 contas + 2 service accounts)")
    Rel(scripts, sinapse, "sinapse_query / save_decision", "MCP")
    Rel(agents, scripts, "Carrega personas sob demanda", "render_agent_prompt.py")
```

## Container Inventory

| Container | Tech | Responsabilidade | WIP/SLA |
|---|---|---|---|
| `scripts/agent_squad.py` | Python | CLI: work items, gates, handoffs, audit | < 2s startup |
| `scripts/azure_devops_project_setup.py` | Python | Provisionar projeto (13 apply_*) | idempotente |
| `scripts/pr_governance.py` | Python | Parsear threads `[NN-] approve|reject` | < 1s/PR |
| `scripts/bdd_runner.py` | Python | Validar BDD Given/When/Then | < 30s/spec |
| `contracts/*.schema.json` | JSON Schema 2020-12 | Validar artefatos | 13 schemas |
| `config/workflow.yaml` | YAML | Estados, gates, wip, halt | 1 source |
| `banco/squad.db` | SQLite WAL | Métricas, símbolos, votes | retenção 400d |

## Fluxos críticos

1. **Orquestrador → ADO**: `python scripts/azure_devops_project_setup.py --apply`
   lê `templates/devops.yaml`, valida contra `devops-config.schema.json`,
   provisiona via REST com a conta apropriada.
2. **PR vote → ledger**: `pr_governance.py` parseia threads, registra em
   `docs/delivery-ledger.md` com `evidence` por work item.
3. **Persona → LLM**: `render_agent_prompt.py` compila o system prompt da
   persona a partir de `PROMPT.md` + skills; chama provider; resposta volta
   como texto estruturado.

## Data flow (auditoria)

```
persona action -> script (REST) -> Azure DevOps
                              -> banco/squad.db
                              -> sinapse (vault)
                              -> docs/delivery-ledger.md
```
