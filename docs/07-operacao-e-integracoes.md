# 07 — Operação, Ferramentas CLI e Motores Funcionais (MCP, Banco & Integrations)

## 1. Setup e Provisionamento Total (Zero-to-Hero)

O Agents Squad pode ser provisionado do zero com um único comando, criando o ambiente virtual isolado (`.venv`), sincronizando repositórios vendor, inicializando o banco de dados `banco/squad.db` e subindo os containers Docker:

```powershell
# Execução do instalador unificado
.\install.ps1
# ou
python scripts/setup_environment.py
```

---

## 2. Motores Funcionais de Inteligência de Código (`integrations/`)

| Motor Funcional | Arquivo | Responsabilidade Canônica |
|---|---|---|
| **Procedural Skill Engine** | `procedural_skill_engine.py` | Síntese de skills `agentskills.io`, linter rígido (anti-shell, anti-marketing) e AST security audit |
| **Trajectory Refinement Engine** | `trajectory_refinement_engine.py` | Rastreamento de passos, categorização de `ErrorCode` e destilação de heurísticas `/refine` |
| **Prompt Quality Optimizer** | `prompt_quality_optimizer.py` | Cálculo do índice de qualidade de prompts (Objetivo, Ground Truth, Antifabricação, Fronteiras) |
| **Blast Radius Analyzer** | `blast_radius_analyzer.py` | Travessia do grafo de dependências para cálculo do raio de impacto |
| **Codebase Knowledge Graph** | `codebase_knowledge_graph.py` | Mapeamento estrutural de símbolos, funções, classes e chamadas |
| **Code Health Analyzer** | `code_health_analyzer.py` | Pontuação de 0.0 a 10.0 baseada em complexidade ciclomática e contratos |
| **Contextual AST Chunker** | `contextual_ast_chunker.py` | Chunking preservando limites sintáticos de funções e classes (cAST) |
| **SDLC Role Mapper** | `sdlc_role_mapper.py` | Mapeamento para 36 papéis compatíveis com IDEs e fluxos de desenvolvimento |

---

## 3. Comandos CLI de Operação

### A. Ciclo de Vida de Skills & Autoaprendizagem
```powershell
# Sintetizar nova skill a partir de work item entregue (/learn)
python scripts/auto_skill_learner.py learn --work-item TASK-001 --name fastapi-jwt-auth --persona software-engineer

# Executar linter rígido de convenções na skill
python scripts/auto_skill_learner.py lint --path skills/discovery/intake/fastapi-jwt-auth/SKILL.md

# Destilar regras de autorreparo de trajetória (/refine)
python scripts/auto_skill_learner.py refine --work-item TASK-001 --agent software-engineer

# Avaliar qualidade de prompt e briefing (BoostPrompt Quality Index)
python scripts/auto_skill_learner.py eval-prompt --path agents/06-software-engineer/PROMPT.md

# Promover skill da quarentena para o catálogo ativo (18-skill-curator)
python scripts/auto_skill_learner.py promote --name fastapi-jwt-auth --domain engineering
```

### B. Inicializar e Gerenciar Work Items
```powershell
# Inicializar work item com governança
python scripts/agent_squad.py init-work-item --id EPIC-EXEMPLO --risk medium

# Compactar memória compartilhada (Anchored Summarization)
python scripts/agent_squad.py compact-memory --work-item work/TASK-001

# Registrar consumo de tokens no banco/squad.db
python scripts/agent_squad.py track-tokens --work-item work/TASK-001 --agent software-engineer --step build --prompt-tokens 1200 --completion-tokens 450 --cost 0.02

# Votação de quórum bizantino em gate
python scripts/agent_squad.py decide-quorum --work-item work/TASK-001 --gate G4-code-security --threshold 0.67
```

### C. Avaliação de Trajetórias e Evals (EDD)
```powershell
# Executar benchmark de convergência e escopo
python scripts/evaluate_agent_trajectories.py --benchmark smoke-test --agent software-engineer
```

### D. Auditoria de Segurança e Sincronização MCP
```powershell
# Varrer scripts e work items por segredos e comandos perigosos
python scripts/audit_security_guardrails.py --target scripts
python scripts/audit_security_guardrails.py --target integrations
python scripts/audit_security_guardrails.py --target banco

# Sincronizar configuração de servidores MCP em config/mcp_config.json
python scripts/sync_mcp_servers.py

# Aplicar o processo Azure DevOps (identidades, cores, pontuação, board, PRs)
# em QUALQUER projeto apontado por .agents_squad/config/devops.yaml / .env
python scripts/azure_devops_project_setup.py --apply --json
```

### E. Execução e Testes com Docker Compose
```powershell
# Iniciar stack Docker com limites rígidos de recursos
docker compose up -d

# Visualizar consumo de memória live (71 MiB core + 69 MiB FalkorDB)
docker stats --no-stream

# Executar comandos e testes dentro do container
docker compose exec squad-core python -m pytest scripts/tests/
```

---

## 4. Service Accounts

Além das identidades humanas e de squad, o Agents Squad opera com service accounts dedicadas em Azure DevOps:

- **`cyber_red@michellaurindooutlook812.onmicrosoft.com`**: Red Team ofensivo. Executa `offensive-cyber-operator` em paths sensíveis (`auth/`, `crypto/`, `iac/`, `*.tf`, `Dockerfile`). Requer double sign-off cross-account com `security-reviewer` (`arthemis@`).
- **`customer_data_pii@michellaurindooutlook812.onmicrosoft.com`**: Leitor de dados PII. Acesso somente leitura a dados sensíveis, sem voto em PR. Utilizado para auditoria e validação de conformidade (LGPD/GDPR).
```
