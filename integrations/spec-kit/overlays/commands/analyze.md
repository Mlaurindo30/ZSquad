---
command: analyze
stage: blueprint
upstream: integrations/spec-kit/upstream/templates/commands/analyze.md
---

# Comando governado: analyze

## Propósito
Auditar consistência cruzada entre especificação, esclarecimentos, plano `{{plan_path}}` e tarefas `{{tasks_path}}`: cobertura de requisitos, dependências, riscos materiais e divergências entre artefatos. O analyze roda ANTES de G3-readiness; é o último ponto de verificação antes da autorização de implementação.

## Entradas obrigatórias (precondições verificáveis)
- project_id: `{{project_id}}`
- work_id: `{{work_id}}`
- plano: `{{plan_path}}`
- tarefas: `{{tasks_path}}`
- evidência válida de G2-design: `{{g2_evidence}}`

Sem plano, tarefas e G2 válido, este comando NÃO executa por confirmação textual do operador.

## Cláusulas Squad (não negociáveis)
- Checklist incompleto não aceita confirmação textual como override; o controle Squad mantém o bloqueio.
- Clarify é obrigatório: avaliação mesmo sem perguntas, registrando que não há bloqueantes.
- TDD/BDD segue a política Squad; a auditoria verifica que cada requisito de código tem tarefa e teste correspondentes. Testes são opcionais apenas nos templates upstream, nunca aqui.
- Achado material aberto no analyze impede G3; registrar o achado com evidência, não apenas opinião.

## Handoff para os gates Squad
Os resultados do analyze entram como evidência em G3-readiness do work item `{{work_id}}`. A autorização real pertence ao gate, não a este prompt.

## Atribuição
Baseado em github/spec-kit (MIT) — adaptado pelo Agents Squad.
