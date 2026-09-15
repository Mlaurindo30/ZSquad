# Registro de Patches — Spec Kit upstream (Tarefa T1)

## Status

**Nenhum patch aplicado nesta tarefa.** O snapshot em `upstream/` é a árvore
literal do commit base `c173bf19a6654e3b05386ec3599349a55282b897`, extraída via
`git archive` sem qualquer modificação local (zero arquivos adicionados,
alterados ou removidos).

## Registro

| Data | Arquivo | Tipo | Justificativa | Autor |
|------|---------|------|---------------|-------|
| (vazio) | — | — | — | — |

## Convenção

Toda futura divergência local sobre a árvore upstream (arquivo alterado,
adicionado ou removido) deve ser registrada aqui com data, tipo, justificativa
e autor, e refletida no manifesto `UPSTREAM_FILES.sha256` somente por meio de
procedimento documentado (ver `docs/plans/2026-09-11-spec-kit-integration.md`,
seção 4). O verificador `verify_snapshot.py` rejeita qualquer divergência não
manifestada.
