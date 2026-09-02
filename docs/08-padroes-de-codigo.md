# 08 — Padrões de Código, Contratos e Evidência

## 1. Princípios de Desenvolvimento Limpo (Clean Code)

1. **Desenvolvimento Orientado a Testes (TDD)**: Toda alteração comportamental deve ser precedida por testes de especificação (*Red-Green-Refactor*).
2. **Contrato de Componente Obrigatório**: Todo módulo, classe ou função não trivial deve conter o bloco canônico de contrato:
   ```text
   O que é: <definição concisa e papel arquitetural>
   Responsabilidade: <uma única responsabilidade verificável>
   Pra que serve: <finalidade no fluxo de negócio e consumidor>
   Comportamento em falha: <tratamento de erro, fallback, logging e resiliência>
   Conexões: <chamadores, eventos, tabelas, APIs e arquivos afetados>
   Dependências & Imports:
     - <lib/módulo>: <justificativa de uso>
   ```
3. **Docstrings Tipadas e Explicativas**: Todas as funções públicas devem documentar parâmetros (`Args`), retornos (`Returns`) e exceções (`Raises`).
4. **Complexidade Cognitiva Limitada**: Funções devem manter complexidade cognitiva $\le 8$ e ciclomática $\le 10$. Evitar aninhamentos profundos e preferir *guard clauses* (retornos antecipados).
5. **Mitigação Anti-Bloat**:
   - Proibição de novas dependências externas sem aprovação prévia.
   - Reuso prioritário de utilitários locais existentes em vez de reimplementação.
   - Proibição de wrappers, adaptadores ou fábricas sem múltiplas implementações reais.
6. **Evidência Obrigatória**: Toda declaração de sucesso ("pronto", "funciona", "corrigido") exige verificações reais executadas por `scripts/quality_gate_runner.py`. A evidência JSON deve cumprir `contracts/verification-evidence.schema.json`, identificar work item, verificador e persona, registrar comando, código de saída, timestamp e hashes de todos os arquivos avaliados.
7. **Validação Fail-Closed**: `decide_gate()` compara os critérios executáveis declarados com `scripts/gate_validators.py`. Evidência ausente, fora do schema, de outro work item, com hash divergente ou resultado incompatível bloqueia a decisão.
8. **TDD Encadeado**: G4 requer `evaluation/tdd/red.json`, `green.json` e `refactor.json`, ligados por digest e pelo mesmo teste e critério de aceite. RED deve falhar; GREEN e REFACTOR devem passar.
9. **BDD e Segregação**: Cenários Gherkin são validados e executados com `pytest-bdd`. Clean Code, testes, segurança e QA usam personas revisoras independentes conforme `config/workflow.yaml`; o autor não pode aprovar artefato de risco médio ou superior.

## Comandos canônicos

```powershell
python scripts/validate_structure.py
python scripts/verify_clean_code.py scripts/*.py integrations/*.py
python scripts/audit_security_guardrails.py --target scripts
python -m pytest scripts/tests/ --cov=scripts --cov=integrations --cov-branch
```

Exemplo de evidência tipada:

```powershell
python scripts/quality_gate_runner.py --work-item TASK-001 --verifier unit-tests --persona test-engineer --output work/TASK-001/evaluation/unit-tests.json --file scripts/module.py -- python -m pytest scripts/tests/test_module.py
```

