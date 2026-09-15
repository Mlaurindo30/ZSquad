# Integração permanente do Spec Kit — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. O processo do Squad, suas personas e gates continuam obrigatórios.

**Goal:** Integrar o código-fonte completo do Spec Kit em integrations e tornar especificação, esclarecimento e planejamento pré-condições verificáveis da implementação governada.

**Architecture:** Snapshot completo e versionado do upstream em integrations/spec-kit/upstream, com adaptações locais em integrations/spec-kit/adapter e overlays. O CLI agent_squad.py continua sendo a entrada canônica; não haverá um segundo orquestrador obrigatório.

**Tech Stack:** Python do runtime Squad; schemas JSON; configuração YAML; documentos Markdown; pytest e BDD existentes. O upstream analisado exige Python >=3.11. Dependências específicas serão isoladas e fixadas; não instalar pacotes no ambiente global.

**Spec:** work/agent_squad/STUDY-SPECKIT-DEEP-20260911/findings/spec-kit-deep-study.md, complementado pela decisão de localização deste plano.

## 1. Estado, autorização e restrições globais

Documento de desenvolvimento, ainda não executado. O usuário determinou integrations como pasta permanente dos projetos integrados; .temp fica exclusivamente para estudo. Essa instrução substitui a opção anterior de distribuir somente templates selecionados: manteremos o fonte completo, mas ativaremos apenas os componentes necessários.

Runtime confirmado: C:/Users/miche/OneDrive/Documentos/agent_squad.
O caminho C:/Users/miche/OneDrive/Documentos/agent/_squad não existe; não criar uma segunda instalação por engano.
Base analisada: github/spec-kit, commit c173bf19a6654e3b05386ec3599349a55282b897.
Não afirmar leitura semântica exaustiva: 567 arquivos tiveram leitura física; os componentes relevantes receberam análise técnica.
Checkout Squad contém alterações preexistentes. Registrar baseline/diff e preservar tudo que não pertencer à integração.
Nenhum gate humano é aprovado por este documento. ACK pendente do estudo não autoriza promover gates.
Manter licença MIT e copyright do upstream. Nunca copiar .env, bancos, ambientes virtuais ou segredos.
Máximo 8 pontos por item, WIP por fase e separação autor/revisor para risco medium.
Não copiar o runtime Squad para os projetos consumidores.
Todas as interfaces e caminhos novos abaixo são propostas a implementar, não comandos existentes.

## 2. Decisão de arquitetura e alternativas

A. Incorporar e executar o CLI Spec Kit inteiro: viável, porém duplica estado/orquestração e não garante Clarify obrigatório.
B. Reescrever tudo em CLI independente: viável, mas duplica manutenção e perde melhorias upstream.
C. Escolhida: fonte upstream completo dentro de integrations + adaptador pequeno comandado pelo CLI Squad.

Ter o projeto inteiro disponível não significa ativar todas as integrações, scripts ou dependências. O upstream completo permite auditoria, comparação e atualização. As mudanças funcionais preferem adapter/overlays; correções inevitáveis no upstream ficam documentadas no registro de patches. Toda edição persistente ocorre em integrations, nunca em .temp.

## 3. Estrutura permanente e propriedade

Novos arquivos/diretórios:
- integrations/spec-kit/README.md: instalação, execução, suporte, limites e manutenção.
- integrations/spec-kit/PROVENANCE.yaml: URL, commit base, licença, hash do manifesto e versão do adaptador.
- integrations/spec-kit/UPSTREAM_FILES.sha256: hashes dos arquivos upstream.
- integrations/spec-kit/PATCHES.md: arquivo, motivo, diff, teste e compatibilidade de cada alteração upstream.
- integrations/spec-kit/upstream/: todos os arquivos versionados da árvore upstream, inclusive documentação, testes e licença.
- integrations/spec-kit/adapter/__init__.py: exports mínimos.
- integrations/spec-kit/adapter/contracts.py: leitura normalizada e tipos.
- integrations/spec-kit/adapter/validation.py: validações estruturais e cobertura.
- integrations/spec-kit/adapter/policy.py: autorização das subetapas.
- integrations/spec-kit/adapter/rendering.py: composição de templates.
- integrations/spec-kit/adapter/backlog.py: projeção de tarefas da fonte canônica.
- integrations/spec-kit/overlays/commands/: constitution, specify, clarify, plan, tasks, implement e analyze adaptados.
- integrations/spec-kit/tests/: testes próprios do adaptador.
- contracts/sdd-package.schema.json: contrato proposto dos artefatos SDD.
- contracts/sdd-policy.schema.json: contrato proposto da política por projeto.
- scripts/tests/test_sdd_gate_enforcement.py: integração com estados/gates.
- docs/spec-kit-operations.md: operação, atualização, diagnóstico e rollback.

Arquivos existentes afetados:
- scripts/agent_squad.py: entrada CLI, avaliação de gates e avanço protegido.
- scripts/gate_validators.py: critérios executáveis alinhados ao workflow.
- scripts/project_context.py: resolução de política por projeto.
- config/workflow.yaml e config/cycles.yaml: subetapas e cobertura dos ciclos.
- contracts/work-item.schema.json: referências/revisões SDD compatíveis com itens legados.
- integrations/README.md: catálogo da integração.
- scripts/tests/test_gate_evidence_enforcement.py e test_work_cycles.py: regressão.
- Prompts/manifests de requirements-analyst, solution-architect, software-engineer e reviewers: contratos de entrega por fase; localizar caminhos reais pelo registry antes de editar.

O diretório com hífen não será importado por nome Python. A entrada Squad carregará adapter como pacote por caminho absoluto usando importlib.util.spec_from_file_location com submodule_search_locations, sem alterar sys.path global. Testar resolução tanto da raiz quanto de outro cwd.

Artefatos por work item ficam em work/<project_id>/<work_id>/sdd/, nunca no vendor:
spec.md, clarifications.yaml, plan.md, tasks.yaml, tasks.md e package.json.
Constituição normativa fica versionada no projeto consumidor, referenciada pelo pacote; memória episódica continua no SQLite. Referências resolvem contra PROJECT_ROOT ou work_dir configurado, não contra cwd.

## 4. Incorporação, versionamento e atualização

Estratégia inicial: vendoring de snapshot completo da árvore Git, sem repositório Git aninhado e sem histórico completo. Isso incorpora o projeto inteiro do commit, não arquivos gerados ou caches.
Na execução, usar git archive do commit verificado para área de staging em integrations/spec-kit, conferir manifesto e promover para upstream. Não mover cegamente .temp, que pode conter arquivos não versionados.
Preservar .temp até terminar verificação; nenhuma exclusão recursiva é necessária para a entrega.
Registrar origin/commit com git -C .temp/spec-kit rev-parse HEAD e git ls-tree antes da extração. A origem do snapshot poderá ser a cópia estudada, desde que o objeto exista e corresponda ao commit fixado.
O manifesto deve enumerar todos os caminhos versionados, hashes e eventuais symlinks/submódulos; ausência ou discrepância bloqueia promoção.
Se houver arquivo acima de 5 MB, não omiti-lo silenciosamente: resolver distribuição compatível com a política de repositório antes do commit.
Não depender de .temp para imports, execução, testes próprios ou documentação operacional.

Atualizações são deliberadas: escolher commit -> ler diferenças/release notes -> staging completo -> aplicar overlays/patches -> executar compatibilidade e regressão -> revisão -> atualizar PROVENANCE. Sem git pull automático nem execução de hooks recebidos.
Rollback usa o snapshot e adaptador anteriormente aprovados; não remove evidências nem revalida aprovações antigas automaticamente.

## 5. Fluxo obrigatório e gates

Constitution -> Specify -> Clarify -> G1-product -> Plan -> G2-design -> Tasks/analyze -> Scaffolding/G3-readiness -> Implement -> G4 -> G5 -> G6.

Manter colunas externas existentes. Dentro de blueprint, persistir subetapas: constitution, specification, clarification, product-review, planning, design-review, tasking. A conclusão de blueprint exige G1 e G2 válidos; G3 autoriza implementação após scaffolding.
G1 avalia produto e dúvidas bloqueantes. G2 avalia arquitetura, interfaces, riscos, testes e blast radius. G3 verifica tarefas, responsáveis, dependências, cobertura e evidência RED.
Clarify exige avaliação mesmo quando não há perguntas. Registrar que não existem bloqueantes e a justificativa. O limite upstream de cinco perguntas não encerra dúvidas restantes.
Constituição aplicável é obrigatória; herança explícita e hash são aceitos, placeholder/N/A não.
TDD/BDD segue a política Squad, independentemente de testes opcionais nos templates upstream.
Checklist incompleto ou achado material aberto não aceita confirmação textual como override de gate.
Pesquisa pode gerar artefatos isolados de estudo. Não autoriza entrega de produto.
Bugfix e new-project devem cumprir preflight proporcional antes de qualquer implementação governada, mesmo que o ciclo antigo entre diretamente em implementation ou termine em scaffolding.

## 6. Contratos propostos e invalidação

package.json:
- schema_version: 1; project_id; work_id; constitution_path; constitution_sha256.
- inputs: mapa de spec, clarifications, plan e tasks para caminho, revision e sha256.
- requirements: IDs estáveis REQ-001 etc., acceptance_ids e classificação.
- reviews: identidade do autor/revisor, resultado, evidências e hashes revisados.

clarifications.yaml:
- questions: id, requirement_ids, severity (blocking/nonblocking), status (open/resolved/accepted_assumption), question, answer, source, owner.
- Uma questão blocking só deixa de bloquear quando resolved e possui resposta/fonte.
- accepted_assumption exige justificativa, responsável e condição de revisão; não disfarça bloqueante aberta.

tasks.yaml:
- id, requirement_ids, acceptance_ids, owner, points, depends_on, paths, evidence, test_ids, status e remote_id quando aplicável.
- points pertence a 1,2,3,5,8; dependências devem existir e formar DAG.
- Requisito de código sem tarefa e teste correspondente bloqueia G3; itens documentais têm evidência de revisão apropriada.
- Não aceitar aprovação apenas pela existência da palavra PASS em Markdown.

Política:
- proposed sdd.required, policy_version e legacy_mode serão resolvidos/validados pelo Squad.
- Piloto ativa required em um projeto; expansão torna obrigatório para novos itens de desenvolvimento.
- Campo ausente em projeto ainda não migrado preserva compatibilidade, mas não pode ser divulgado como cobertura SDD obrigatória.
- Em projeto ativado, policy inválida, ausente ou ilegível falha fechada.
- Desativação exige alteração de política revisada; não é fallback silencioso.

Decisões de gate vinculam work_id, gate_id, input hashes, policy_version, decider e evidência. Identidade e human_approval respeitam contratos atuais.
Mudança de spec invalida G1 e dependentes; mudança de plan invalida G2 e dependentes; mudança de tasks invalida G3 e dependentes. Constituição ou política alterada exige reavaliação de todos os gates afetados.
Verificar também imediatamente antes do dispatch. Atualização de estado usa lock/releitura e escrita atômica para evitar corrida entre avaliação e avanço.

Interfaces novas:
- load_package(work_dir: Path) -> dict
- validate_package(package: dict, stage: str) -> list[dict] (code, path, message)
- authorize(package: dict, stage: str, decisions: list[dict], policy: dict) -> list[dict]
- render_command(stage: str, context: dict) -> str
- project_tasks(items: list[dict]) -> str

Lista vazia significa ausência de erros estruturais; não substitui revisão semântica humana/especializada. Erros estáveis: SDD_MISSING_INPUT, SDD_OPEN_QUESTION, SDD_STALE_GATE, SDD_COVERAGE_GAP, SDD_DEPENDENCY_CYCLE, SDD_POLICY_INVALID.

## 7. Backlog de desenvolvimento e ordem

Estimativa inicial, a validar no sizing de cada item. Épico tamanho G; itens separados, nunca somados em uma única story de mais de 8 pontos.
Criar itens/handoffs somente pela CLI agent_squad.py, consultando --help atual; não fabricar IDs aprovados ou comandos.
Na ativação de cada especialista: persona -> manifest -> skills -> pesquisa oficial -> DevOps. Briefing de oito blocos, uma persona por agente.
Para cada tarefa comportamental: teste falhando -> implementar mínimo -> teste passando -> refatorar -> revisão independente -> commit seletivo quando autorizado.

### T1 — Snapshot permanente verificável (3 pts)
Owner integration-engineer; reviewer code-reviewer. Dependências: nenhuma.
Arquivos: upstream/, PROVENANCE.yaml, UPSTREAM_FILES.sha256, PATCHES.md, README.md.
- [ ] Registrar baseline do checkout e proveniência do commit.
- [ ] Extrair árvore completa em integrations e gerar manifesto sem executar scripts upstream.
- [ ] Verificar igualdade de caminhos/hash com o objeto Git, licença e ausência de caches/segredos.
- [ ] Testar fixture com arquivo alterado: a verificação precisa rejeitar a divergência.
- [ ] Documentar snapshot aprovado e procedimento de reprodução.
Aceite: cópia completa verificável e sem dependência operacional de .temp.

### T2 — Pacote e contratos SDD (5 pts)
Owner backend-engineer; reviewer code-reviewer. Depende T1.
Arquivos: contracts.py, validation.py, schemas novos e integrations/spec-kit/tests/test_contracts.py.
Consome paths do work item; produz load_package e validate_package.
- [ ] Escrever testes para input ausente, ID duplicado, path fora da raiz, dúvida bloqueante, ciclo e points=13.
- [ ] Executar python -m pytest integrations/spec-kit/tests/test_contracts.py -q e capturar RED.
- [ ] Implementar leitura JSON/YAML segura e validação dos contratos da seção 6; recusar path traversal e YAML com objetos executáveis.
- [ ] Cobrir pacote válido e caminho com espaços/acentos no Windows; capturar GREEN.
Exemplo de contrato de teste:
```python
def test_missing_spec_is_rejected(valid_package):
    valid_package["inputs"].pop("spec")
    errors = validate_package(valid_package, "planning")
    assert any(e["code"] == "SDD_MISSING_INPUT" for e in errors)
```
Fixture valid_package deve materializar os quatro documentos e hashes temporários; não usar arquivos de produção.

### T3 — Templates e seis comandos governados (5 pts)
Owner integration-engineer; reviewer code-reviewer. Depende T2.
Arquivos: rendering.py, overlays/commands/*.md, tests/test_rendering.py.
Consome contexto validado; produz render_command.
- [ ] Testar que plan exige spec/clarifications/G1 e implement exige plan/tasks/G1-G3.
- [ ] Executar python -m pytest integrations/spec-kit/tests/test_rendering.py -q; registrar RED.
- [ ] Compor comandos preservando licença, referências ao work item, TDD/BDD e papel do agente.
- [ ] Remover bypass de checklist dos overlays; incluir Clarify obrigatório e analyze antes de G3.
- [ ] Validar renderização de todos os sete comandos, paths Windows e ausência de referências a .temp; registrar GREEN.
Aceite: prompts têm os contratos; a autorização real ainda depende de T4/T5.

### T4 — Política, identidade e hashes de gates (8 pts)
Owner backend-engineer; reviewer security-reviewer. Depende T2.
Arquivos: policy.py, project_context.py, work-item.schema.json, tests/test_policy.py.
Consome package/policy/decisions; produz authorize.
- [ ] Testar aprovação de outro work item, input alterado, autor igual a revisor, policy inválida e evidência ausente.
- [ ] Executar python -m pytest integrations/spec-kit/tests/test_policy.py -q; capturar RED.
- [ ] Implementar autorização por fase com regras de risco/humano atuais, sem inventar identidade por nome em arquivo.
- [ ] Implementar invalidação transitiva e falha fechada de política ativada.
- [ ] Rodar testes negativos e positivos; capturar GREEN.
```python
def test_changed_spec_invalidates_plan(valid_package, approved_decisions, required_policy):
    valid_package["inputs"]["spec"]["sha256"] = "0" * 64
    errors = authorize(valid_package, "planning", approved_decisions, required_policy)
    assert any(e["code"] == "SDD_STALE_GATE" for e in errors)
```

### T5 — State machine, dispatch e ciclos (8 pts)
Owner backend-engineer; reviewer code-reviewer. Depende T3,T4.
Arquivos: agent_squad.py, gate_validators.py, workflow.yaml, cycles.yaml, test_sdd_gate_enforcement.py, test_work_cycles.py.
- [ ] Criar testes CLI: G1 sem G2 não conclui blueprint; G3 ausente não inicia implementação; bugfix/retomada não contornam preflight.
- [ ] Executar python -m pytest scripts/tests/test_sdd_gate_enforcement.py scripts/tests/test_work_cycles.py -q; capturar RED.
- [ ] Integrar authorize no avanço e antes do dispatch gerenciado; alinhar critérios configurados com todos os validadores executáveis.
- [ ] Adicionar lock, releitura dos hashes e escrita atômica; rejeitar alteração concorrente.
- [ ] Testar chamada direta, cwd alternativo, policy ativada e legado desativado; capturar GREEN.
Aceite: um erro não altera estado nem despacha executor. activation_packet pode gerar contexto de leitura, mas não conceder autorização de escrita.

### T6 — Tarefas, cobertura e backlog (5 pts)
Owner integration-engineer; reviewer qa-engineer. Depende T2,T5.
Arquivos: backlog.py, tests/test_backlog.py, documentação operacional.
- [ ] Testar requisito sem tarefa/teste, dependência inexistente, conflito de remote_id e indisponibilidade do Boards.
- [ ] Executar python -m pytest integrations/spec-kit/tests/test_backlog.py -q; registrar RED.
- [ ] Implementar project_tasks como projeção determinística; preservar integração DevOps existente sem criar conexão paralela.
- [ ] Com Boards habilitado, tasks.md é visão gerada e IDs remotos são canônicos; sem Boards, tasks.yaml é canônico.
- [ ] Nunca promover snapshot remoto vencido a evidência de aprovação; testar GREEN.
Aceite: cobertura auditável e nenhum segundo backlog editável conflitante. Writes externos dependem de autorização explícita.

### T7 — Piloto e regressão de segurança (5 pts)
Owner test-engineer; reviewer qa-engineer. Depende T5,T6.
Arquivos: fixtures e cenários em integrations/spec-kit/tests/, scripts/tests/test_sdd_gate_enforcement.py.
- [ ] Montar projeto temporário de teste isolado, sem usar credenciais ou alterar um projeto real.
- [ ] Executar cenários da seção 8 em user-story, new-project e bugfix.
- [ ] Testar adulteração de documento após gate e imediatamente antes de dispatch.
- [ ] Executar suites focais, regressão scripts/tests e auditorias Squad em sequência; registrar comandos, saídas e limitações.
- [ ] Corrigir falhas antes de declarar aceite; após duas tentativas sem solução, reportar bloqueio e hipóteses testadas.
Aceite: provas positivas e negativas, sem afirmar validação live de DevOps se não executada.

### T8 — Operação, atualização e rollout (3 pts)
Owner devops-release-engineer; reviewer governance-auditor. Depende T7.
Arquivos: docs/spec-kit-operations.md, integrations/README.md, PROVENANCE.yaml.
- [ ] Documentar instalação isolada, política, comandos efetivamente implementados e recuperação.
- [ ] Ensaiar troca entre dois snapshots em fixture e rollback preservando artefatos.
- [ ] Registrar critérios de adoção legada: baseline mínima revisada antes da próxima implementação; sem backfill de aprovações.
- [ ] Executar validação estrutural e auditoria; anexar resultados ao handoff.
- [ ] Submeter G6 conforme risco e autorização humana; não publicar nem habilitar globalmente automaticamente.

## 8. Aceite BDD e evidência

1. Given spec ausente, When solicitar plan, Then rejeitar sem mudar estado.
2. Given dúvida bloqueante aberta, When solicitar G1, Then rejeitar com ID da dúvida.
3. Given G1 válido/G2 ausente, When concluir blueprint, Then bloquear.
4. Given spec alterada após aprovação, When retomar, Then marcar gates dependentes desatualizados.
5. Given tarefa sem requisito ou requisito de código sem teste, When solicitar G3, Then rejeitar.
6. Given pacote aprovado, RED válido e responsáveis definidos, When implementar, Then despachar somente tarefas elegíveis e respeitar WIP.
7. Given chamada direta/bugfix/new-project, When tentar pular preflight, Then aplicar a mesma política.
8. Given checklist incompleto e usuário responde continuar ao prompt, Then o controle Squad mantém bloqueio.
9. Given Boards indisponível, When validar tarefa remota, Then não aceitar dados antigos como aprovação.
10. Given atualização upstream incompatível, When testar staging, Then manter versão anterior ativa.
11. Given projeto legado sem ativação, When consultar, Then preservar compatibilidade e sinalizar ausência de garantia SDD.
12. Given agente com acesso irrestrito edita código manualmente, When abrir PR, Then CI rejeita falta de evidências; prevenção da escrita em si requer sandbox.

Verificação final prevista: python scripts/validate_structure.py; python scripts/agent_squad.py audit; python -m pytest scripts/tests/ -q; python -m pytest integrations/spec-kit/tests/ -q.
Verificar argumentos atuais por --help antes da execução. Nenhuma dessas execuções futuras é declarada como resultado deste plano.
A suíte upstream precisa ambiente isolado com dependências declaradas; estudo anterior falhou na coleta por json5 ausente. Não confundir compileall com teste funcional.

## 9. Rollout, métricas e Definition of Done

Fases: incorporação fonte -> validação em fixture -> um projeto piloto -> revisão dos resultados -> expansão revisada.
Registrar bloqueios corretos/falsos positivos, tempo de esclarecimento, retrabalho de requisitos, cobertura e defeitos de aceitação. Sem metas numéricas inventadas antes da baseline.
Conclusão exige T1–T8 verificadas, revisão independente, critérios BDD aprovados, licenças/proveniência preservadas, operação sem .temp e gates humanos aplicáveis.
Documentar limitações residuais: revisão semântica não é prova automática; agentes fora do dispatcher podem editar arquivos se têm permissão; CI/PR impede aceitação, não toda escrita.
Não desligar política silenciosamente para contornar falha. Rollback reverte código/configuração com revisão e preserva histórico; trabalhos iniciados sob outra versão passam por reavaliação.
Nenhum tempo de calendário é prometido; pontos são estimativa inicial de complexidade.

## 10. Verificação deste documento

Cobertura: seis pilares T2/T3; destino permanente T1; imposição real T4/T5; rastreabilidade T6; evidência T7; operação T8.
Este plano não moveu nem modificou o Spec Kit, não alterou o runtime e não criou aprovações. A implementação começa por T1 no diretório integrations, preservando a cópia de estudo.

