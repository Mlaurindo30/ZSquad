---
command: clarify
stage: blueprint
upstream: integrations/spec-kit/upstream/templates/commands/clarify.md
---

# Comando governado: clarify

## Propósito
Avaliar a especificação `{{spec_path}}` e registrar dúvidas e decisões em `{{clarifications_path}}`: id, severidade (blocking/nonblocking), status (open/resolved/accepted_assumption), pergunta, resposta, fonte e responsável.

## Entradas obrigatórias
- project_id: `{{project_id}}`
- work_id: `{{work_id}}`
- especificação: `{{spec_path}}`
- registro de esclarecimentos: `{{clarifications_path}}`

## Cláusulas Squad (não negociáveis)
- Clarify é obrigatório: avaliação mesmo sem perguntas, registrando que não há bloqueantes. Ausência de perguntas é um resultado válido e deve ser registrado como tal.
- O limite upstream de cinco perguntas não encerra dúvidas restantes; dúvidas bloqueantes abertas impedem G1.
- `accepted_assumption` exige justificativa, responsável e condição de revisão; não disfarça bloqueante aberta.
- Checklist incompleto não aceita confirmação textual como override; o controle Squad mantém o bloqueio.
- TDD/BDD segue a política Squad; testes são opcionais apenas nos templates upstream, nunca aqui.

## Handoff para os gates Squad
Com as dúvidas resolvidas ou assumidas com justificativa, o work item `{{work_id}}` torna-se elegível a G1-product. A autorização real pertence ao gate, não a este prompt.

## Atribuição
Baseado em github/spec-kit (MIT) — adaptado pelo Agents Squad.
