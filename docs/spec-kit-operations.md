# Operação da Integração Spec Kit (SDD) — Agents Squad

> **Escopo deste documento**: operação, política por projeto, comandos implementados,
> verificação, atualização de snapshot, diagnóstico, rollback, adoção legada e
> limitações residuais da integração `integrations/spec-kit/`.
>
> **Procedência**: documentado na Tarefa T8 (`work/agent_squad/TASK-SPECKIT-T8-20260911`)
> conforme `docs/plans/2026-09-11-spec-kit-integration.md` (§4, §7-T8, §9).
> Todo comando listado foi executado ou verificado via `--help` nesta sessão
> (2026-09-11). O que não foi executado está marcado como **não executado nesta sessão**.

---

## 1. Instalação isolada

A integração vive exclusivamente em `integrations/spec-kit/` do runtime Squad
(`C:/Users/miche/OneDrive/Documentos/agent_squad`). Não há cópia do runtime nos
projetos consumidores e não há dependência operacional de `.temp/`.

Estrutura permanente:

```text
integrations/spec-kit/
├── upstream/               # snapshot completo do commit c173bf19a6654... (567 arquivos, MIT)
├── UPSTREAM_FILES.sha256   # manifesto SHA-256 estilo git (567 entradas)
├── PROVENANCE.yaml         # origem, commit, licença, hash do manifesto, adapter_version
├── PATCHES.md              # registro de divergências locais sobre a árvore upstream (vazio)
├── verify_snapshot.py      # verificador árvore x manifesto
├── adapter/                # adapter SDD (contracts, validation, policy, rendering, backlog)
├── overlays/commands/      # 7 comandos governados (constitution, specify, clarify, plan, tasks, implement, analyze)
├── tests/                  # suíte comportamental do adapter e do snapshot
└── README.md               # instalação e manutenção do snapshot
```

Propriedades de isolamento verificadas:

- O diretório `integrations/spec-kit` contém hífen e **não é importável pelo nome
  Python**. O adapter é carregado via `importlib.util.spec_from_file_location` com
  `submodule_search_locations`, **sem alterar `sys.path` global** (ver docstring de
  `adapter/__init__.py`).
- Nenhum script upstream é executado pelo runtime.
- O CLI canônico continua sendo `python scripts/agent_squad.py`. Não existe um
  segundo orquestrador obrigatório.
- Os artefatos SDD de cada work item ficam em `work/<project_id>/<WORK-ID>/sdd/`
  (`spec.md`, `clarifications.yaml`, `plan.md`, `tasks.yaml`, `tasks.md`,
  `package.json`), nunca dentro do vendor.
- A constituição normativa do projeto consumidor fica versionada no projeto,
  referenciada pelo pacote com caminho + `constitution_sha256`.
- Os schemas dos contratos ficam em `contracts/sdd-package.schema.json` e
  `contracts/sdd-policy.schema.json`.

Nota de ambiente: o upstream analisado declara Python >= 3.11. As suítes locais
foram executadas nesta sessão com Python 3.13 (Windows). Não instalar pacotes no
ambiente global; as dependências dos testes upstream ficam isoladas e **não são
executadas pelo runtime** (ver §9).

---

## 2. Política SDD por projeto

A política SDD é resolvida por projeto consumidor a partir de
`<project_root>/.agents_squad/config/sdd-policy.yaml`
(`scripts/project_context.py`, constante `SDD_POLICY_REL`), validada contra
`contracts/sdd-policy.schema.json`:

```yaml
policy_version: 1        # inteiro >= 1
sdd:
  required: true         # pacote SDD obrigatório para work items de desenvolvimento
# legacy_mode: false     # opcional; projetos não migrados preservam compatibilidade
```

Regras efetivas (implementadas em T4/T5, verificadas pelos testes
`integrations/spec-kit/tests/test_policy.py` e
`scripts/tests/test_sdd_gate_enforcement.py`):

1. **Projeto sem política** (`sdd.required` ausente): modo legado — compatibilidade
   preservada, mas **não** pode ser divulgado como cobertura SDD obrigatória.
2. **Projeto ativado** (`sdd.required: true`): política inválida, ausente ou
   ilegível **falha fechada** (`SDD_POLICY_INVALID`). Nunca prossegue sem política.
3. **Desativação legítima** é `sdd.required: false` numa política revisada
   (mudança de política com revisão); **não existe fallback silencioso** e desligar
   a política para contornar uma falha é proibido.
4. Itens pré-ativação em projeto ativado são **BLOQUEADOS** (fail-closed, decisão
   do T5) até migração manual ou ajuste de política — ver §8.

---

## 3. Comandos efetivamente implementados

Todos verificados via `python scripts/agent_squad.py <subcomando> --help` nesta
sessão. A ajuda completa do CLI lista os subcomandos disponíveis:
`python scripts/agent_squad.py --help`.

### 3.1 `decide-gate` — registrar decisão de gate

```text
usage: agent_squad.py decide-gate [-h] --work-item WORK_ITEM --gate GATE
                                  --decider DECIDER
                                  --criteria CRITERIA [CRITERIA ...]
                                  --evidence EVIDENCE [EVIDENCE ...]
                                  [--human-approved-by HUMAN_APPROVED_BY]
                                  [--human-evidence HUMAN_EVIDENCE]
                                  [--author AUTHOR] [--reviewer REVIEWER]
```

Em gates SDD, `--author` (autor da entrega) e `--reviewer` (revisor independente)
são **obrigatórios** — a decisão emitida vincula autor/revisor, hashes dos inputs
e `policy_version`. Critérios usam o formato `nome=pass|fail|not_applicable`.
Aprovação por mera existência da palavra "PASS" em Markdown não é aceita.

### 3.2 `advance-state` — avanço protegido de estado

```text
usage: agent_squad.py advance-state [-h] --work-item WORK_ITEM
```

Avança deterministicamente o estado do work item seguindo
`config/cycles.yaml`. Em projeto com política SDD ativa, o avanço executa a
autorização SDD (`adapter.authorize`) com **lock, releitura dos hashes e escrita
atômica** de `status.yaml` — rejeita alteração concorrente e um erro SDD_* não
altera estado.

### 3.3 `run-engine` — despacho gerenciado de motor de integração

```text
usage: agent_squad.py run-engine [-h] --engine ENGINE --work-item WORK_ITEM
```

Executa `integrations/<engine>.py` (ou `integrations/experimental/<engine>.py`)
com **preflight de despacho**: com política ativa, o estágio do estado atual
precisa estar autorizado antes de qualquer execução; bugfix/retomada não contornam
o preflight; work item não resolvível na base governada **aborta o despacho**
(fail-closed, MAJOR-1). Erros `SDD_*` abortam sem despachar executor.

### 3.4 `activate-agent` — packet de ativação

```text
usage: agent_squad.py activate-agent [-h] --agent AGENT
                                     [--work-item WORK_ITEM]
                                     [--assigned [ASSIGNED ...]]
                                     [--discovered [DISCOVERED ...]]
```

Gera contexto de leitura (persona, manifest, skills). **Não** concede autorização
de escrita: a autorização de implementação vem dos gates SDD via
`decide-gate`/`advance-state`/`run-engine`.

### 3.5 Interfaces do adapter (superfície programática)

Definidas no plano §6 e implementadas em `integrations/spec-kit/adapter/`
(`__init__.py` exporta `load_package` e `validate_package`; os demais módulos são
carregados pelo CLI Squad):

| Interface | Assinatura | Função |
|---|---|---|
| `load_package` | `(work_dir: Path) -> dict` | leitura normalizada do pacote SDD do work item |
| `validate_package` | `(package: dict, stage: str) -> list[dict]` | validação estrutural; retorna erros `{code, path, message}` |
| `authorize` | `(package: dict, stage: str, decisions: list[dict], policy: dict) -> list[dict]` | autorização da subetapa com invalidação transitiva |
| `render_command` | `(stage: str, context: dict) -> str` | composição dos prompts governados (overlays) |
| `project_tasks` | `(items: list[dict]) -> str` | projeção determinística de tarefas/backlog |

Lista vazia significa ausência de erros estruturais e **não substitui revisão
semântica humana/especializada**.

---

## 4. Verificação da operação

Sequência de verificação executada nesta sessão (2026-09-11), com saídas
verbatim registradas em
`work/agent_squad/TASK-SPECKIT-T8-20260911/traceability/progress-T8-20260911.md`:

```bash
python scripts/validate_structure.py
# VALID structure agents=41 active_skills=148 schemas=15   (exit 0)

python scripts/agent_squad.py audit
# 255 x "skill ativa fora do catálogo" — TODAS sob integrations/spec-kit/ (exit 1; ver §6.1)

python -m pytest integrations/spec-kit/tests/ -q
# 166 passed, 1 skipped in 3.60s

python -m pytest scripts/tests/ -q
# 899 passed, 4 skipped, 10 warnings in 287.37s (0:04:47)

python integrations/spec-kit/verify_snapshot.py
# OK: snapshot íntegro (UPSTREAM_FILES.sha256)   (exit 0)
```

`verify_snapshot.py` pode ser chamado com raiz e manifesto explícitos
(`python integrations/spec-kit/verify_snapshot.py [raiz_snapshot] [manifesto]`);
exit codes: `0` íntegro, `1` divergência, `2` uso inválido. Divergências
reportadas: `MISSING`, `MISMATCH`, `EXTRA` (e symlinks rejeitados como `EXTRA`).

---

## 5. Atualização de snapshot (procedimento deliberado)

Atualizações são **deliberadas** — sem `git pull` automático e sem execução de
hooks recebidos (plano §4). Ordem obrigatória:

1. **Escolher o commit** alvo no upstream (`github/spec-kit`).
2. **Ler diferenças e release notes** entre o commit base atual
   (`c173bf19a6654e3b05386ec3599349a55282b897`) e o alvo; decidir impacto em
   adapter/overlays.
3. **Staging completo**: extrair a árvore integral do commit alvo via
   `git archive` para área de staging (mesmo método do T1); conferir contra
   `git ls-tree -r`; registrar origin/commit antes da extração.
4. **Aplicar overlays e patches**: reimpor os 7 comandos governados e registrar
   qualquer correção inevitável no upstream em `PATCHES.md` (data, arquivo,
   motivo, diff, teste, compatibilidade).
5. **Compatibilidade e regressão**: regenerar `UPSTREAM_FILES.sha256` para o novo
   staging; executar `verify_snapshot.py`, `pytest integrations/spec-kit/tests/ -q`
   e `pytest scripts/tests/ -q`.
6. **Revisão independente** (revisor distinto do executor — segregação de papéis).
7. **Atualizar `PROVENANCE.yaml`**: novo commit base, novo hash do manifesto,
   contagem de arquivos, data e bump de `adapter_version` quando o adapter mudar.

O manifesto deve enumerar todos os caminhos versionados com hashes; ausência ou
discrepância **bloqueia a promoção**. Arquivo acima de 5 MB não é omitido
silenciosamente. Nenhuma edição persistente fora de `integrations/`.

### 5.1 Ensaio de troca de snapshot e rollback (executado em 2026-09-11)

Ensaio executado em diretório temporário **fora do repositório**
(`C:/Users/miche/AppData/Local/Temp/speckit-t8-rehearsal-JhCiGy`, removido após o
ensaio). Comandos e resultados verbatim:

```bash
# 1. Copiar a árvore duas vezes (A = versão aprovada atual; B = "nova versão")
REHEARSAL=$(mktemp -d -t speckit-t8-rehearsal-XXXXXX)
cp -r integrations/spec-kit "$REHEARSAL/A"
cp -r integrations/spec-kit "$REHEARSAL/B"

# 2. Simular novo commit: modificar um arquivo upstream em B e regenerar o manifesto de B
echo "# linha simulando novo commit upstream (ensaio T8)" >> "$REHEARSAL/B/upstream/README.md"
# Regenerar manifesto de B (mesmo formato "<sha256>  <caminho>", 567 entradas):
python - <<'PY'
import hashlib, pathlib
root = pathlib.Path("$REHEARSAL/B")  # ajuste para o caminho real do ensaio
lines = [f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(root/'upstream').as_posix()}"
         for p in sorted((root/"upstream").rglob("*")) if p.is_file()]
(root/"UPSTREAM_FILES.sha256").write_text("\n".join(lines) + "\n", encoding="utf-8")
PY

# 3. Verificação cruzada — prova de segurança do rollback:
python integrations/spec-kit/verify_snapshot.py "<temp>/A/upstream" "<temp>/A/UPSTREAM_FILES.sha256"
# OK: snapshot íntegro (UPSTREAM_FILES.sha256)      (exit 0)

python integrations/spec-kit/verify_snapshot.py "<temp>/B/upstream" "<temp>/A/UPSTREAM_FILES.sha256"
# FALHA: 1 divergência(s) entre árvore e manifesto
# MISMATCH: README.md                                 (exit 1)

# 4. Ciclo completo da troca: B validada pelo PRÓPRIO manifesto regenerado (B/B)
python integrations/spec-kit/verify_snapshot.py "<temp>/B/upstream" "<temp>/B/UPSTREAM_FILES.sha256"
# OK: snapshot íntegro (UPSTREAM_FILES.sha256)      (exit 0)
# (reexecutado pelo orchestrator em 2026-09-11: A/A exit 0; B/B exit 0; B-vs-A exit 1)
```

**Prova de segurança**: um snapshot novo/divergente **não pode ser validado
silenciosamente pelo manifesto antigo** — a verificação cruza falha com
`MISMATCH`. Rollback reutilizável: restaurar a cópia A (snapshot + adaptador
anteriormente aprovados) e o manifesto dela; a verificação volta a dar exit 0.

---

## 6. Diagnóstico

### 6.1 Ruído conhecido do `audit` (baseline)

`python scripts/agent_squad.py audit` reporta nesta baseline **255** apontamentos
"skill ativa fora do catálogo", **todos** sob `integrations/spec-kit/` (arquivos
upstream + adapter/tests). Classe conhecida e esperada: o scanner de catálogo de
skills varre `integrations/` e não distingue vendor governado de skill não
catalogada.

**Tratamento**: os apontamentos são aceitos como baseline documentada desta
integração (esta seção) e NÃO são silenciosamente ignorados. Decisão pendente de
governança (não executada nesta sessão): (a) exclusão de
`integrations/spec-kit/` do escopo do scanner de catálogo, com referência a este
documento, ou (b) aceite formal recorrente pelo `governance-auditor` no G6. O
audit sai com exit 1 por causa desses apontamentos; qualquer apontamento **fora**
de `integrations/spec-kit/` é regressão real e deve ser tratado.

### 6.2 Erros SDD_* e seus significados

Códigos definidos e aplicados em `integrations/spec-kit/adapter/`:

| Código | Significado | Tratamento típico |
|---|---|---|
| `SDD_MISSING_INPUT` | input obrigatório do estágio ausente do pacote (ex.: plan exigindo `spec`) | produzir o documento e atualizar `package.json` com hash |
| `SDD_OPEN_QUESTION` | dúvida bloqueante `open` em `clarifications.yaml` | resolver a questão (resposta + fonte) ou aceitar como suposição com justificativa |
| `SDD_STALE_GATE` | input alterado após aprovação — gate/dependentes desatualizados (invalidação transitiva) | reavaliar e reemitir as decisões afetadas |
| `SDD_COVERAGE_GAP` | requisito de código sem tarefa/teste correspondente (bloqueia G3) | completar `tasks.yaml` / testes |
| `SDD_DEPENDENCY_CYCLE` | `depends_on` inexistente ou em ciclo | corrigir o DAG em `tasks.yaml` |
| `SDD_POLICY_INVALID` | política SDD inválida, ausente ou ilegível em projeto ativado (fail-closed) | corrigir `.agents_squad/config/sdd-policy.yaml` conforme schema |
| `SDD_MALFORMED` | estrutura malformada (violação de schema, path traversal não verificável, estágio desconhecido, pacote não-objeto) | corrigir o pacote/documento apontado em `path` |
| `SDD_REMOTE_CONFLICT` | `remote_id` duplicado/inconsistente entre tarefas e snapshot do Boards | reconciliar `tasks.yaml` com o Boards |
| `SDD_REMOTE_STALE` | snapshot remoto oferecido com o Boards indisponível — nunca promovido a evidência de aprovação | refazer a validação com o Boards disponível |

Em todos os casos: o erro **não altera estado nem despacha executor**. Sem o
Boards habilitado, `tasks.yaml` é canônico; com Boards, IDs remotos são canônicos
e `tasks.md` é visão gerada.

---

## 7. Rollback

Procedimento (plano §4, §9), com ensaio executado em §5.1:

1. **Reverter para o snapshot e adaptador anteriormente aprovados** (cópia A do
   ensaio): restaurar `upstream/`, `UPSTREAM_FILES.sha256`, `adapter/`,
   `overlays/` e `PROVENANCE.yaml` da versão aprovada anterior e confirmar com
   `verify_snapshot.py` (exit 0) + suítes de regressão.
2. **Preservar evidências**: o rollback **não remove** evidências de work items
   (`work/<project_id>/<WORK-ID>/sdd/`, gate-decisions, ledger) e **não revalida
   aprovações antigas automaticamente**.
3. **Reavaliação obrigatória**: trabalhos iniciados sob outra versão do
   snapshot/adapter passam por reavaliação dos gates afetados (constituição ou
   política alterada exige reavaliação de todos os gates afetados).
4. Rollback reverte código/configuração **com revisão**; não é operação automática
   e não dispensa gates humanos aplicáveis.

---

## 8. Critérios de adoção legada

Registrados conforme §7-T8 (item 3) do plano:

- **Baseline mínima revisada antes da próxima implementação governada** em projeto
  ativado: nenhum item de desenvolvimento governado inicia sem o pacote SDD da
  baseline revisado (spec, clarifications, plan, tasks com cobertura REQ→tarefa→teste).
- **Sem backfill de aprovações**: aprovações passadas não são reemitidas nem
  retroativamente atribuídas ao fluxo SDD; gates legados permanecem legados.
- **Itens pré-ativação em projeto ativado são BLOQUEADOS** (fail-closed, decisão
  do T5): work items iniciados antes da ativação da política não avançam nem
  despacham até migração manual para o formato SDD ou ajuste explícito e revisado
  da política. Não existe bypass por prompt, palavra-chave ou resposta "continuar".
- Projeto legado **sem** ativação: compatibilidade preservada (modo legado), com
  sinalização de ausência de garantia SDD (cenário BDD 11 do plano).

---

## 9. Limitações residuais (documentadas, não resolvidas)

1. **Revisão semântica não é prova automática**: listas vazias de erros
   estruturais não substituem revisão humana/especializada do conteúdo.
2. **Agentes fora do dispatcher podem editar arquivos** se têm permissão de
   sistema de arquivos: o controle Squad impede *aceitação* de escrita não
   governada, não a escrita em si; prevenção total requer sandbox.
3. **CI/PR impede aceitação, não toda escrita**: evidências faltantes rejeitam o
   PR, mas não impedem que um agente com acesso irrestrito edite código manualmente
   (cenário BDD 12 do plano).
4. **Nenhuma validação live de Azure DevOps foi executada** (T7/T8): o piloto
   rodou 12/12 cenários §8 em fixtures isoladas; integração real com Boards/PRs
   não foi exercitada nesta sessão e permanece pendente para o rollout.
5. **Testes upstream não são executados pelo runtime**: exigem ambiente isolado
   próprio com dependências declaradas (census registrou falha de coleta por
   `json5` ausente). `compileall` não é teste funcional.
6. **Piloto em um projeto**: a expansão da política `sdd.required` para novos
   itens depende de revisão dos resultados do piloto (plano §9); nada foi
   habilitado globalmente nesta sessão.
7. **G6 não submetido nesta sessão**: a submissão é humana-gated; este documento
   e os anexos no work item preparam a submissão, não a executam.

---

## 10. Referências

- Plano: `docs/plans/2026-09-11-spec-kit-integration.md` (§4, §7-T8, §9)
- Snapshot: `integrations/spec-kit/README.md`, `integrations/spec-kit/PROVENANCE.yaml`
- Contratos: `contracts/sdd-package.schema.json`, `contracts/sdd-policy.schema.json`
- Work item: `work/agent_squad/TASK-SPECKIT-T8-20260911/`
  (evidência verbatim em `traceability/progress-T8-20260911.md`)
- Aprovação humana: `work/agent_squad/TASK-SPECKIT-T8-20260911/HUMAN-APPROVAL-20260911.md`
