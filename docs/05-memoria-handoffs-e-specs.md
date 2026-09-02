# 05 — Memória, Handoffs e Decisões de Gate

## Memória do Squad

A memória do Agents Squad é estruturada em dois níveis:

1. **Memória de Longo Prazo (Durable Vault)**: Gerenciada pelo protocolo Hive-Mind / Sinapse em `D:/Hive-Mind/cerebro/`.
2. **Memória do Work Item**: Localizada em `work/<WORK-ID>/memory/`:
   - `shared/summary.md`: Fatos e decisões publicados e confirmados.
   - `agents/<persona>.md`: Checkpoints privados da persona.
   - `deltas/MEM-*.yaml`: Deltas tipados com `kind: fact | decision | dependency | risk | pending`.

## Contrato de Handoff

Nenhuma entrega é válida sem um `handoffs/HANDOFF-*.yaml` completo conforme `contracts/handoff.schema.json`:
- Link para os artefatos produzidos (caminho absoluto).
- Evidência de execução (comando, código de saída, saída real).
- Delta de memória vinculado.
- Próximo gate e próximo agente responsável.
- Confirmação de aceite (`acknowledgement`).

## Decisões de Gate

Cada gate avaliado gera um arquivo `gate-decisions/GD-<WORK-ID>-<GATE>.yaml` com:
- Critérios avaliados (`pass | fail | not_applicable`).
- Bloco de aprovação humana quando obrigatório.
