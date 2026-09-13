---
name: azure-devops-board-management
description: >
  Protocolo operacional completo para interação dos agentes com o Azure DevOps Boards:
  criação de Work Items, movimentação de cards pelas 7 colunas SDLC, atualização de estados,
  aplicação de tags obrigatórias, vinculação pai-filho (Epic→Feature→Story→Task) e
  uso correto de contas ADO (squads@ vs arthemis@).
version: "1.0"
applies_to: [00-delivery-orchestrator, 02-product-owner, 03-scrum-master, 13-devops-release-engineer]
---

# Azure DevOps Board Management

## Overview

Este protocolo define a operação e gestão obrigatória do **Azure DevOps Boards** para todos os agentes do Agents Squad. Ele estabelece os procedimentos para planejamento, criação, vinculação, movimentação e encerramento de cards, integrando o fluxo de engenharia dos agentes diretamente à plataforma corporativa Azure DevOps.

---

## 1. Regra Inegociável de Centralização no Board

Quando o arquivo `devops.yaml` (ou `.agents_squad/config/devops.yaml`) estiver presente e configurado no projeto:

> [!IMPORTANT]
> **Todo backlog, planejamento, refinamento e rastreamento de progresso vive EXCLUSIVAMENTE no Azure DevOps Boards.**
> 
> É **PROIBIDO** criar arquivos como `product-goal.md`, `backlog.md`, `board.yaml`, `task_plan.md` ou `delivery-plan.md` locais como substitutos ou espelhos paralelos de cards do Azure DevOps.
> 
> O Azure Boards é a **única fonte da verdade** para o estado e ciclo de vida do trabalho.

---

## 2. Topologia e Hierarquia de 4 Níveis

A organização dos work items deve seguir estritamente a hierarquia corporativa de 4 níveis:

```
🔶 Epic (Laranja)      — Visão macro e objetivo estratégico do produto
  └── 🟣 Feature (Roxo)     — Capacidade funcional/técnica mensurável (FEAT-NN)
        └── 🔷 User Story (Azul) — Entrega de valor vertical testável (US-NN, ≤ 8 pts)
              └── 🟡 Task (Amarelo)    — Tarefas técnicas específicas das 7 fases SDLC
```

### Regras de Hierarquia:
1. **NUNCA** criar uma User Story diretamente vinculada a um Epic (sem Feature intermediária).
2. **NUNCA** criar uma Task diretamente abaixo de um Epic ou Feature. Toda Task pertence a uma User Story.
3. User Stories devem respeitar o teto cognitivo de **Story Points Fibonacci $\le 8$** (1, 2, 3, 5 ou 8). Histórias maiores que 8 pontos devem ser divididas pelo `40-agile-coach` antes da criação das Tasks.

---

## 3. Modelo de Contas e Segregação de Funções (SoD)

O acesso e a autoria das ações no Azure DevOps obedecem ao princípio de Segregação de Funções (ISO/IEC 27001 A.5.3, SOC 2 CC6.1, NIST SP 800-53 CM-5) estruturado em **4 contas de automação dedicadas** + 1 supervisão humana:

| Conta ADO | Papel no Ciclo | Responsabilidades e Personas |
|---|---|---|
| `squads@michellaurindooutlook812.onmicrosoft.com` | **Execução Técnica / Dev** | Criação de cards, movimentação em fases de construção, execução de Tasks, commits de código (`00`, `01`–`08`, `15`–`27`, `29`–`33`, `35`, `37`–`41` — 38 personas). |
| `arthemis@michellaurindooutlook812.onmicrosoft.com` | **Revisão e Aprovação / SoD** | Revisão obrigatória de PRs, avaliação e emissão de Gates G4, G5 e G6, encerramento formal de cards e stories (`09-code-reviewer`, `10-security-reviewer`, `12-qa-engineer`, `28-performance-engineer`, `14-governance-auditor` — 5 personas). |
| `cyber-red@michellaurindooutlook812.onmicrosoft.com` | **Segurança Ofensiva / Red Team** | Duplo sign-off obrigatório cross-account com `security-reviewer` em arquivos sensíveis (`/auth/`, `/crypto/`, `/iac/`, `*.tf`, `Dockerfile`). Conta dedicada do `34-offensive-cyber-operator`. |
| `customer_data_pii@michellaurindooutlook812.onmicrosoft.com` | **Dados Sensíveis / PII** | Acesso e leitura exclusiva a datasets e pipelines com dados confidenciais/PII. Segregação por projeto; **sem voto em PR**. |
| `human_master` (`michel.laurindo@outlook.com`) | **Supervisão Humana** | Aprovação de CAB, deploy produtivo, alteração de credenciais. **Nunca atribuir cards de rotina a esta conta.** |

---

## 4. As 7 Colunas SDLC e Mapeamento de Estados

O Kanban do produto possui 7 colunas determinísticas. O avanço de qualquer card deve refletir a correspondência entre a coluna visual do Board e o estado nativo (`System.State`) do Work Item:

| Coluna do Board | Fase SDLC | Estado ADO (`System.State`) | Conta Responsável | Personas Típicas |
|---|---|---|---|---|
| **Blueprint** | Concepção, BDD, ADR | `New` | `squads@` | `02-product-owner`, `04-solution-architect`, `03-scrum-master` |
| **Scaffolding** | Testes RED, contratos | `Active` | `squads@` | `00-delivery-orchestrator`, `06-software-engineer` |
| **Implementation** | TDD Green/Refactor | `Active` | `squads@` | `06-software-engineer`, `21-frontend-engineer`, `22-backend-engineer` |
| **Code Security Review** | Análise estática, Gate G4 | `Active` | `arthemis@` | `10-code-reviewer`, `28-offensive-cyber-operator` |
| **Quality Validation** | BDD, regressão, Gate G5 | `Resolved` | `arthemis@` | `11-qa-engineer`, `12-performance-engineer` |
| **Governance Release** | Ledger, rollout/rollback, G6 | `Resolved` | `arthemis@` | `14-governance-auditor`, `13-devops-release-engineer` |
| **Done** | Concluído / Fechado | `Closed` | `arthemis@` | `14-governance-auditor` (encerramento formal) |

---

## 5. Protocolo de Criação de Work Items

A criação de itens deve ser feita via MCP `@azure-devops/mcp` usando a ferramenta `wit_work_item_write`.

### 5.1 Campos Obrigatórios na Criação

1. `System.Title`: Padrão `[TIPO-NN] Título descritivo` (ex: `[FEAT-01] Autenticação Multi-Tenant`, `[US-04] Login via OAuth2`).
2. `System.AreaPath`: Exclusivo do produto (ex: `Arthemis\agent-squad` ou conforme configurado em `devops.yaml`).
3. `System.IterationPath`: Sprint ativa (ex: `Arthemis\Iteration 1`).
4. `Microsoft.VSTS.Scheduling.StoryPoints`: Valor Fibonacci (1, 2, 3, 5 ou 8) obrigatório para User Stories.
5. `System.Tags`: Pelo menos 3 tags estruturadas:
   - `phase-<nome>` (ex: `phase-blueprint`)
   - `squad-<agente-id>` (ex: `squad-02-product-owner`)
   - `story-points-<N>` (ex: `story-points-3`)
6. `System.Description`: **Header obrigatório** com badge do agente ativo:
   ```markdown
   > **Active Agent: [02-product-owner]**

   ### Contexto & Objetivo
   ...
   ```
7. `System.AssignedTo`: Atribuir à conta correta (`squads@` para execução ou `arthemis@` para revisores).

### 5.2 Exemplo de Criação via MCP (`wit_work_item_write`)

```json
{
  "project": "Arthemis",
  "type": "User Story",
  "fields": {
    "System.Title": "[US-18] Gerenciamento de Cards no Azure DevOps Boards",
    "System.AreaPath": "Arthemis\\agent-squad",
    "System.IterationPath": "Arthemis\\Sprint 1",
    "System.Description": "> **Active Agent: [02-product-owner]**\n\n### User Story\nComo operador do squad,\nQuero sincronizar estados no Azure Boards,\nPara manter governança centralizada sem artefatos locais paralelos.",
    "Microsoft.VSTS.Scheduling.StoryPoints": 3,
    "System.Tags": "phase-blueprint; squad-02-product-owner; story-points-3",
    "System.AssignedTo": "squads@michellaurindooutlook812.onmicrosoft.com"
  }
}
```

---

## 6. Protocolo de Movimentação de Cards

O fluxo visual deve refletir o ciclo de vida em tempo real:

1. **Antes de Iniciar o Trabalho**:
   - Mover o card para a coluna da fase correspondente ANTES de começar a codificar ou redigir artefatos.
   - Atualizar tanto `System.BoardColumn` quanto `System.State` e reatribuir o `System.AssignedTo` se houver troca de papel (execução $\leftrightarrow$ revisão).
2. **Durante o Trabalho**:
   - Manter a tag de fase atualizada (remover tag antiga e incluir nova `phase-<nome>`).
3. **Ao Concluir a Fase**:
   - Registrar um comentário de evidência via MCP antes de passar para a próxima coluna, contendo o resultado da fase (ex: logs de testes, hash do commit, link do PR ou Parecer Técnico de Gate).

### 6.1 Exemplo de Atualização de Coluna e Estado

```json
{
  "id": 1042,
  "fields": {
    "System.BoardColumn": "Implementation",
    "System.State": "Active",
    "System.Tags": "phase-implementation; squad-06-software-engineer; story-points-3",
    "System.AssignedTo": "squads@michellaurindooutlook812.onmicrosoft.com"
  }
}
```

---

## 7. Protocolo de Vinculação Pai-Filho (Hierarchy Links)

Nenhum card deve ficar órfão no Board. As relações devem ser estabelecidas na criação ou imediatamente após:

- `Task` $\rightarrow$ `Parent: User Story`
- `User Story` $\rightarrow$ `Parent: Feature`
- `Feature` $\rightarrow$ `Parent: Epic`

### Tipo de Link do Azure DevOps:
- Link para pai: `System.LinkTypes.Hierarchy-Reverse`
- Link para filho: `System.LinkTypes.Hierarchy-Forward`

### Exemplo de Vínculo via MCP (`wit_work_item_write`):

```json
{
  "id": 1045,
  "links": [
    {
      "rel": "System.LinkTypes.Hierarchy-Reverse",
      "url": "https://dev.azure.com/cbvgas/Arthemis/_apis/wit/workItems/1042",
      "attributes": {
        "comment": "Parent User Story"
      }
    }
  ]
}
```

---

## 8. Ferramentas MCP `@azure-devops/mcp`

Os agentes interagem com o Azure DevOps Boards através do servidor MCP dedicado:

| Ferramenta MCP | Finalidade | Exemplo de Uso |
|---|---|---|
| `wit_work_item` | Consulta detalhes, estado, links e campos de um card | Ler aceitação da US antes de implementar |
| `wit_work_item_write` | Cria novos work items ou atualiza campos, estados e links | Criar Task, mover coluna, adicionar comentário |
| `wit_query` | Executa consultas estruturadas em linguagem WIQL | Listar cards da Sprint atual em determinada fase |
| `repo_pull_request_write` | Cria ou atualiza Pull Requests com reviewers SoD | Abrir PR associando ao ID do Work Item |

### Exemplo de Consulta WIQL (`wit_query`):

```json
{
  "query": "SELECT [System.Id], [System.Title], [System.State], [System.BoardColumn] FROM WorkItems WHERE [System.AreaPath] = 'Arthemis\\agent-squad' AND [System.IterationPath] = @CurrentIteration AND [System.WorkItemType] = 'User Story' ORDER BY [Microsoft.VSTS.Common.BacklogPriority] ASC"
}
```

---

## 9. Uso de Swimlanes no Board

O Board possui duas raias (swimlanes) configuradas:

1. **Standard** (Padrão):
   - Utilizada para todo o fluxo normal de desenvolvimento (User Stories, Tasks e Features planejadas).
2. **Expedite** (Urgência / Incidente):
   - Reservada exclusivamente para correções de emergência em produção (`bugfix` express, hotfix, vulnerabilidade crítica `severity: high|critical`).
   - Cards na raia `Expedite` têm prioridade absoluta sobre o WIP do squad e bypassan colunas não essenciais conforme definido no ciclo `incident` de `cycles.yaml`.

Para mover um card para a raia Expedite:
```json
{
  "id": 1050,
  "fields": {
    "System.BoardRow": "Expedite"
  }
}
```

---

## 10. Checklist de Qualidade do Card (Definition of Card)

Antes de considerar um card devidamente criado ou movido:

- [ ] Título estruturado com prefixo canônico (`[EPIC-NN]`, `[FEAT-NN]`, `[US-NN]`, `[TASK-NN]`).
- [ ] Vínculo hierárquico com o item pai configurado (`Hierarchy-Reverse`).
- [ ] `AreaPath` e `IterationPath` apontando para o time e sprint corretos.
- [ ] Badge do agente ativo presente no topo da descrição (`> **Active Agent: [NN-persona-id]**`).
- [ ] Estimativa em Story Points (Fibonacci 1–8) preenchida para User Stories.
- [ ] Tags obrigatórias aplicadas (`phase-*`, `squad-*`, `story-points-*`).
- [ ] Conta atribuída correspondente ao tipo de fase (`squads@` para execução, `arthemis@` para revisão).
