---
command: constitution
stage: blueprint
upstream: integrations/spec-kit/upstream/templates/commands/constitution.md
---

# Comando governado: constitution

## Propósito
Estabelecer ou reafirmar a constituição normativa do projeto consumidor: princípios, restrições não negociáveis e critérios de qualidade que os demais comandos SDD devem respeitar. A constituição é versionada no projeto consumidor e referenciada pelo pacote SDD; nunca é editada dentro do vendor em `integrations/spec-kit/upstream/`.

## Entradas obrigatórias
- project_id: `{{project_id}}`
- work_id: `{{work_id}}`
- constituição aplicável: `{{constitution_path}}`

## Cláusulas Squad (não negociáveis)
- Constituição aplicável é obrigatória; herança explícita e hash são aceitos, placeholder ou "N/A" não.
- Checklist incompleto não aceita confirmação textual como override; o controle Squad mantém o bloqueio.
- Clarify é obrigatório: avaliação mesmo sem perguntas, registrando que não há bloqueantes.
- TDD/BDD segue a política Squad; testes são opcionais apenas nos templates upstream, nunca aqui.

## Handoff para os gates Squad
Este comando não conclui etapa por si só. O resultado alimenta o pacote SDD do work item `{{work_id}}`; os gates G1–G3 são avaliados pelo CLI Squad. A autorização real de avanço pertence aos gates, não a este prompt.

## Atribuição
Baseado em github/spec-kit (MIT) — adaptado pelo Agents Squad.
