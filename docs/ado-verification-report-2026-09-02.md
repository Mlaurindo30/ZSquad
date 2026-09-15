# Relatório Completo — Azure DevOps: O que foi feito, o que está certo, o que está errado

**Data**: 2026-09-02
**Método**: REST API via `Basic base64(':PAT')`, URL `dev.azure.com/cbvgas`
**Org**: cbvgas | **Project**: Arthemis | **Repo**: agent-squad (c9e2c146)

---

## Score: 29/40 checks (72%)

---

## O QUE ESTÁ CERTO ✅

| Área | Status | Detalhes |
|---|---|---|
| **1 Project** | ✅ | Arthemis (278f0a09) — wellFormed |
| **Repo agent-squad** | ✅ | ID c9e2c146, defaultBranch=refs/heads/master |
| **Board Stories** | ✅ | 7 colunas corretas (Blueprint→Done) com WIPs |
| **CODEOWNERS** | ✅ | No master com todos os paths corretos |
| **5 Branch Policies** | ✅ | File size, Min reviewers, Work item linking, Merge strategy, Copilot |
| **Areas** | ✅ | Root area "Arthemis" existe — work items têm Area=Arthemis |
| **Iterations** | ✅ | Iteration 1 existe (defaultIteration do Arthemis Team) |
| **Work Items** | ✅ | 6 items existentes (Calculator App Epic + 5 related) |
| **Team Settings** | ✅ | Arthemis Team com backlog visibility, working days |
| **Backlog Config** | ✅ | Epics/Features/Requirements category OK |
| **MCP kilo.json** | ✅ | Corrigido com `ADO_MCP_AUTH_TOKEN` |

---

## O QUE ESTÁ ERRADO ❌

### 1. TRÊS TEAMS DESNECESSÁRIOS CRIADOS ❌

```
❌ Squad Core    (251b2e17)
❌ Squad Web     (8f12468d)
❌ Squad AI      (a806b5c7)
```

**Board é por PROJETO, não por team.** O Arthemis Team é o único team funcional.
Esses 3 teams extras foram criados pelo lifecycle script sem confirmação e não servem para nada sem boards/areas/iterations próprios. Só ocupam espaço.

**Work items existentes** (6 items do Calculator App) estão todos no `Arthemis Team`:
```
[1] Epic "Calculator App"       — Area=Arthemis Iter=Arthemis
[2] US "US-CALCULATOR-001"      — Area=Arthemis Iter=Arthemis
[3] Task "CalculatorEngine"      — Area=Arthemis Iter=Arthemis
[4] Task "testes unitarios"    — Area=Arthemis Iter=Arthemis (Closed)
[5] US "US-CALCULATOR-002"      — Area=Arthemis Iter=Arthemis
[6] Task "CalculatorGUI"        — Area=Arthemis Iter=Arthemis
```

**Não há items nos 3 teams extras.**

---

### 2. MINIMUM REVIEWERS POLICY MAL CONFIGURADA ❌

```
Policy id=3: Minimum number of reviewers
  isBlocking: true ✅
  minimumReviewerCount: NOT SET  ❌ ← não exige número mínimo
  requiredReviewerIds: []         ❌ ← nenhum reviewer específico
  resetOnSourcePush: true ✅
```

A policy existe e bloqueia merge, mas não exige reviewers específicos.

**Ação manual necessária**: Portal → Project Settings → Repos → Policies → Editar "Minimum number of reviewers":
1. Setar `minimumReviewerCount = 1`
2. Adicionar `arthemis@michellaurindooutlook812.onmicrosoft.com` como required reviewer

---

### 3. CODEOWNERS NÃO ADICIONA REVIEWERS AUTOMATICAMENTE ❌

O arquivo existe no master com todos os paths mapeados:
```
/scripts/azure_devops*.py   → @arthemis/code-reviewer
/.github/workflows/           → @arthemis/code-reviewer
/auth/ /crypto/ /iac/       → @arthemis/security-reviewer + @cyber_red/offensive-cyber-operator
/contracts/ /config/          → @arthemis/governance-auditor
/scripts/tests/ /tests/       → @arthemis/qa-engineer
*                             → @squads/development-team
```

**Mas o Azure DevOps NÃO adiciona esses reviewers automaticamente.**

**Ação manual necessária**: Portal → agent_squad repo → Settings → Pull Requests → "Automatically add code reviewers from CODEOWNERS" = ON

---

### 4. SWIMLANES NÃO CONFIGURADAS ❌

Board rows API retorna só 1 row (default vazia).
Swimlanes devem separar squads no board compartilhado, mas a API do Azure DevOps **não suporta criação via REST** — só via portal manual.

**Ação manual necessária**: Board Stories → Column Options → Swimlanes → Add swimlane:
- Squad Core, Squad Web, Squad AI, Squad Mobile, Squad Data, Squad AI, Squad Infra-Cloud, Squad Quality, Without Squad

---

### 5. SERVICE CONNECTIONS NÃO CRIADAS ❌

Endpoints testados — todos retornam HTTP -1 (exception):
```
/serviceConnections:                          HTTP -1
/distributedtask/serviceconnections:          HTTP -1
/distributedtask/serviceendpoints:            HTTP -1
```

O `devops.yaml` define 4 service connections com placeholders:
```yaml
Azure-ARMTemplate:  subscription_id: XXXXX...
GitHub-Arthemis:    token: ""
DockerHub-Public:    username: "" password: ""
AKS-Cluster:        kubeconfig: ""
```

**Não podem ser criadas sem credenciais reais.**

---

### 6. ITERATIONS PARA SQUAD CORE/WEB/AI NÃO EXISTEM ❌

O Arthemis Team tem `Iteration 1` como defaultIteration.
Mas os 3 teams extras (Squad Core, Squad Web, Squad AI) têm HTTP -1 em iterations — não foram configurados.

---

### 7. MCP AUTH quebrado no session atual ❌

O `kilo.json` foi corrigido para usar `ADO_MCP_AUTH_TOKEN` + `--authentication envvar`, mas a **sessão atual não recarregou** o config. As tools `azure-devops_*` ainda não estão disponíveis.

---

## Resumo: O que fazer

### 🔴 CRÍTICO — Impede SoD/PR model

| # | O que | Onde | Como |
|---|---|---|---|
| 1 | Setar `minimumReviewerCount=1` + adicionar `arthemis@` | Portal → Policies → id=3 | Manual |
| 2 | Habilitar "Automatically add code reviewers" | Portal → agent_squad repo → Settings | Manual |

### 🟡 ALTO — Funcionalidade básica

| # | O que | Onde | Como |
|---|---|---|---|
| 3 | Configurar iterations nos 3 teams extras | Portal → Teams → Iterations | Manual (se os teams forem necessários) |
| 4 | Configurar swimlanes | Portal → Board → Column Options | Manual |
| 5 | Recarregar sessão Kilo para ativar MCP | — | Reiniciar Kilo |
| 6 | Criar service connections | Portal → Project Settings → Service Connections | Com credenciais reais |

### 🟢 BAIXA PRIORIDADE

| # | O que |
|---|---|
| 7 | Remover 3 teams extras (Squad Core/Web/AI) — não têm função |
| 8 | Dashboard/Wiki/Delivery Plans — `enabled: false` no devops.yaml |
| 9 | Build pipelines — nenhuma definida |

---

## Root Cause dos problemas

1. **Teams extras**: lifecycle script criou sem confirmação, sem pedir
2. **Minimum reviewers**: API não permite setar `requiredReviewerIds` sem Graph scope — configuração manual necessária
3. **CODEOWNERS auto-reviewers**: feature do Azure DevOps que precisa ser habilitada no repo settings
4. **Swimlanes**: API não suporta criação — recurso de portal manual
5. **Service connections**: placeholder credentials no devops.yaml
6. **MCP**: config corrigido mas sessão não recarregou

---

## O que NÃO foi feito que deveria ter sido

1. **Confirmar antes de criar 3 teams** — board é por projeto
2. **Configurar Area paths** (já existia "Arthemis" por padrão do projeto)
3. **Verificar se iterations já existiam** antes de tentar criar
4. **Documentar que swimlanes precisam de ação manual**
5. **Testar PR model end-to-end** com um PR real
