---
command: specify
stage: blueprint
upstream: integrations/spec-kit/upstream/templates/commands/specify.md
---

# Comando governado: specify

## Propósito
Redigir a especificação do requisito em `{{spec_path}}`: problema, escopo, requisitos estáveis (IDs `REQ-NNN`), critérios de aceite (`AC-NNN`) e classificação (code/documentation). A especificação é a fonte canônica; alterá-la invalida G1 e os gates dependentes.

## Entradas obrigatórias
- project_id: `{{project_id}}`
- work_id: `{{work_id}}`
- especificação: `{{spec_path}}`

## Cláusulas Squad (não negociáveis)
- Checklist incompleto não aceita confirmação textual como override; o controle Squad mantém o bloqueio.
- Clarify é obrigatório: avaliação mesmo sem perguntas, registrando que não há bloqueantes.
- TDD/BDD segue a política Squad; requisitos de código nascem com critérios verificáveis, não com testes "se sobrar tempo".

## Handoff para os gates Squad
A especificação alimenta Clarify e depois G1-product (avaliação de produto e dúvidas bloqueantes) no work item `{{work_id}}`. O comando não autoriza implementação; o avanço é decidido pelos gates do CLI Squad.

## Atribuição
Baseado em github/spec-kit (MIT) — adaptado pelo Agents Squad.
