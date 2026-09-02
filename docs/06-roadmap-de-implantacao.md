# 06 — Roadmap e Boas Práticas Operacionais

## Boas Práticas de Execução

1. **Viés de Implementação**: O objetivo é construir e entregar incrementos verificáveis, evitando re-planejamentos desnecessários quando o plano já estiver aprovado.
2. **Convergência**: Verificar uma vez com evidência real. Se uma verificação falhar, tentar no máximo duas vezes antes de emitir bloqueio com a causa concreta.
3. **Segregação de Funções**: Manter a independência entre o implementador e o revisor em riscos médio, alto e crítico.
4. **Sem Alucinações**: Registrar `NOT FOUND` ou `UNVERIFIED` explicitamente quando não houver dados, sem inventar resultados.
