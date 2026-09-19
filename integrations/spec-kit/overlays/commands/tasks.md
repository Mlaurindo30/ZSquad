---
command: tasks
stage: blueprint
upstream: integrations/spec-kit/upstream/templates/commands/tasks.md
---

# Comando governado: tasks

## Propósito
Decompor o plano aprovado `{{plan_path}}` em tarefas em `{{tasks_path}}`: id, requirement_ids, acceptance_ids, owner, points (1, 2, 3, 5, 8), depends_on (DAG), paths, evidence, test_ids e status. Todo requisito de código recebe tarefa e teste correspondentes.

## Entradas obrigatórias (precondições verificáveis)
- project_id: `{{project_id}}`
- work_id: `{{work_id}}`
- plano: `{{plan_path}}` (pré-condição)
- evidência válida de G2-design: `{{g2_evidence}}` (pré-condição)

Sem plano aprovado e sem G2 válido, este comando NÃO executa: o tasking não pode ser iniciado por confirmação textual do operador.

## Cláusulas Squad (não negociáveis)
- Checklist incompleto não aceita confirmação textual como override; o controle Squad mantém o bloqueio.
- Clarify é obrigatório: avaliação mesmo sem perguntas, registrando que não há bloqueantes.
- TDD/BDD segue a política Squad: cada tarefa de código referencia o teste que a verifica; itens documentais têm evidência de revisão apropriada. Testes são opcionais apenas nos templates upstream, nunca aqui.
- Não aceitar aprovação apenas pela existência da palavra PASS em Markdown.

## Handoff para os gates Squad
As tarefas alimentam analyze (consistência) e G3-readiness (cobertura, responsáveis, dependências, evidência RED) no work item `{{work_id}}`. A autorização real pertence ao gate, não a este prompt.

## Atribuição
Baseado em github/spec-kit (MIT) — adaptado pelo Agents Squad.
