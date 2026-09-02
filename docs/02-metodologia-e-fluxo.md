# 02 — Metodologia e Fluxo de Entrega

## Ciclo de Vida da Entrega

O fluxo de entrega do Agents Squad divide-se em 6 fases sequenciais governadas por gates:

```text
[Discovery] ──> [Architecture] ──> [Readiness] ──> [Build] ──> [Quality] ──> [Release]
     │                 │                 │            │           │             │
  (G1-prod)        (G2-design)       (G3-ready)    (G4-code)   (G5-qual)     (G6-rel)
```

### 1. Discovery & Definição de Produto (G1-product)
- **Papéis**: `requirements-analyst`, `product-owner`.
- **Artefatos**: `discovery/brief.md`, `epic.md`, `stories/US-*.md`, `product-goal.md`, `backlog.md`.
- **Critério de Saída**: Histórias INVEST com critérios de aceite testáveis e aprovação humana.

### 2. Arquitetura, Dados & Segurança (G2-design)
- **Papéis**: `solution-architect`, `data-ai-architect`, `data-architect`, `security-reviewer`.
- **Artefatos**: `specs/architecture.md`, `adr/ADR-*.md`, `specs/threat-model.md`, `specs/data-contract.md`.
- **Critério de Saída**: Modelo C4, ADRs fundamentados, STRIDE threat model e estratégia de rollback.

### 3. Prontidão Operacional (G3-readiness)
- **Papéis**: `delivery-orchestrator`, `scrum-master`.
- **Artefatos**: `plans/delivery-plan.md`, `status.yaml`.
- **Critério de Saída**: Definition of Ready atendida, alocação de especialistas e skills resolvíveis.

### 4. Construção & Revisão Segura (G4-code-security)
- **Papéis**: `software-engineer`, `backend-engineer`, `frontend-engineer`, `data-engineer`, `ai-engineer`, `code-reviewer`, `security-reviewer`.
- **Artefatos**: Código-fonte, testes unitários/integrados, `reviews/code-review.md`, `reviews/security-review.md`.
- **Critério de Saída**: Testes passando, cobertura comprovada, ausência de CVEs/vulnerabilidades e contratos de componentes preenchidos.

### 5. Qualidade & Validação E2E (G5-quality)
- **Papéis**: `test-engineer`, `qa-engineer`, `performance-engineer`.
- **Artefatos**: `tests/test-plan.md`, `evidence/test-execution.md`, `reports/qa-report.md`.
- **Critério de Saída**: Testes E2E, testes de carga/latência e caminhos de falha validados com evidência.

### 6. Governança, Auditoria & Release (G6-governance-release)
- **Papéis**: `devops-release-engineer`, `sre-observability-engineer`, `governance-auditor`.
- **Artefatos**: `release/release-record.md`, `documentation/delivery-ledger.md`, `gate-decisions/GD-*.yaml`.
- **Critério de Saída**: Rastreabilidade 100% verificada, telemetria pronta, rollback testado e aprovação humana final.
