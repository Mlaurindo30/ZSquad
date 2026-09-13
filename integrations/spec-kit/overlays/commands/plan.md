---
command: plan
stage: blueprint
upstream: integrations/spec-kit/upstream/templates/commands/plan.md
---

# Comando governado: plan

## Propósito
Produzir o plano de implementação em `{{plan_path}}` a partir da especificação aprovada: arquitetura, interfaces, riscos, blast radius, estratégia de testes e impactos em esquema/credenciais/custo, quando aplicáveis.

## Entradas obrigatórias (precondições verificáveis)
- project_id: `{{project_id}}`
- work_id: `{{work_id}}`
- especificação: `{{spec_path}}` (pré-condição)
- esclarecimentos: `{{clarifications_path}}` (pré-condição; sem dúvida bloqueante aberta)
- evidência válida de G1-product: `{{g1_evidence}}` (pré-condição)

Sem especificação, sem esclarecimentos ou sem G1 válido, este comando NÃO executa: a etapa planning não pode ser iniciada por confirmação textual do operador.

## Cláusulas Squad (não negociáveis)
- Checklist incompleto não aceita confirmação textual como override; o controle Squad mantém o bloqueio.
- Clarify é obrigatório: avaliação mesmo sem perguntas, registrando que não há bloqueantes.
- TDD/BDD segue a política Squad; o plano define cenários Given/When/Then e o teste RED antes de qualquer código. Testes são opcionais apenas nos templates upstream, nunca aqui.
- Alterar a especificação depois deste plano invalida G1 e o plano derivado.

## Handoff para os gates Squad
O plano alimenta G2-design (arquitetura, interfaces, riscos, testes, blast radius) no work item `{{work_id}}`. A autorização real pertence ao gate, não a este prompt.

## Atribuição
Baseado em github/spec-kit (MIT) — adaptado pelo Agents Squad.
