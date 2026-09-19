# Spec Kit — Snapshot upstream governado

Este diretório contém o snapshot permanente e verificável da árvore upstream do
[Spec Kit](https://github.com/github/spec-kit) (GitHub), incorporado ao runtime
Agents Squad na Tarefa T1 (`work/agent_squad/TASK-SPECKIT-T1-20260911`), com
proveniência auditável.

## O que é

- `upstream/` — cópia completa da árvore versionada do commit base
  `c173bf19a6654e3b05386ec3599349a55282b897` (567 arquivos, licença MIT em
  `upstream/LICENSE`).
- `UPSTREAM_FILES.sha256` — manifesto SHA-256 estilo git (`<hex>  <caminho>`,
  caminhos POSIX, ordenado) com uma entrada por arquivo upstream (567 entradas).
- `PROVENANCE.yaml` — origem, commit base, método de extração, licença, hash do
  manifesto, versão do adaptador (0.1.0) e data de extração (2026-09-11).
- `PATCHES.md` — registro de patches locais. Em T1: nenhum patch aplicado.
- `verify_snapshot.py` — verificador da integridade árvore x manifesto.
- `tests/test_snapshot.py` — testes comportamentais do verificador.

## Como foi criado

Extração determinística via `git archive` a partir do checkout de estudo
`.temp/spec-kit` (read-only), sem executar qualquer script upstream:

```
git -C .temp/spec-kit archive c173bf19a6654e3b05386ec3599349a55282b897 | tar -x -C <staging>
```

O staging foi conferido contra `git ls-tree -r` (mesmo conjunto de caminhos),
verificado (567 arquivos, sem `.git`) e promovido para `upstream/`. Nenhum
arquivo foi copiado diretamente de `.temp`.

## Como verificar

```
python integrations/spec-kit/verify_snapshot.py          # exit 0 = íntegro
python -m pytest integrations/spec-kit/tests/ -q        # 5 passed
```

O verificador compara caminhos e hashes entre `upstream/` e o manifesto e
rejeita divergências: arquivo alterado (MISMATCH), não manifestado (EXTRA),
ausente (MISSING).

## Limites

- O snapshot não contém histórico git, submódulos nem repositórios aninhados.
- Os testes upstream exigem ambiente isolado próprio e não são executados pelo
  runtime; durante o censo do checkout registrou-se falha de coleta por
  dependência ausente (`json5`) — isso não afeta a integridade do snapshot.
- Nenhum script upstream é executado pelo runtime.
- Este snapshot não depende operacionalmente de `.temp`.

## Manutenção

Atualizações seguem o procedimento deliberado da seção 4 do plano
`documentation/plans/2026-09-11-spec-kit-integration.md` (escolha de commit,
staging completo, overlays/patches, compatibilidade, revisão, atualização do
PROVENANCE). Sem `git pull` automático e sem execução de hooks recebidos.
