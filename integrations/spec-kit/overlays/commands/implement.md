---
command: implement
stage: implementation
upstream: integrations/spec-kit/upstream/templates/commands/implement.md
---

# Comando governado: implement

## Propósito
Executar as tarefas elegíveis de `{{tasks_path}}` conforme o plano `{{plan_path}}`: implementação TDD (RED primeiro, depois GREEN, depois refactor) e cenários BDD dos critérios de aceite, respeitando WIP e responsáveis definidos.

## Entradas obrigatórias (precondições verificáveis)
- project_id: `{{project_id}}`
- work_id: `{{work_id}}`
- plano: `{{plan_path}}` (pré-condição)
- tarefas: `{{tasks_path}}` (pré-condição)
- evidência válida de G1-product: `{{g1_evidence}}` (pré-condição)
- evidência válida de G2-design: `{{g2_evidence}}` (pré-condição)
- evidência válida de G3-readiness: `{{g3_evidence}}` (pré-condição)

Sem plano, tarefas ou qualquer uma das evidências G1–G3, este comando NÃO executa: a implementação não pode ser iniciada por confirmação textual do operador, nem por retomada de ciclo.

## Cláusulas Squad (não negociáveis)
- Checklist incompleto não aceita confirmação textual como override; o controle Squad mantém o bloqueio.
- Clarify é obrigatório: avaliação mesmo sem perguntas, registrando que não há bloqueantes.
- TDD/BDD segue a política Squad: teste falhando antes de código, cenários BDD para critérios de aceite. Testes são opcionais apenas nos templates upstream, nunca aqui.
- Executar somente tarefas elegíveis do pacote SDD do work item `{{work_id}}`; tarefa fora do pacote ou com dependência não satisfeita não entra na execução.
- Alterar spec, plano ou tarefas durante a execução invalida os gates correspondentes e exige reavaliação.

## Handoff para os gates Squad
A execução produz evidências para G4-code-security, G5-quality e G6-governance-release no work item `{{work_id}}`. A autorização real de avanço e de PR pertence aos gates do CLI Squad, não a este prompt.

## Atribuição
Baseado em github/spec-kit (MIT) — adaptado pelo Agents Squad.
