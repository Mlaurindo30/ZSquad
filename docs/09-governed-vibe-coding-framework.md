# 09 — Framework de Governed Vibe Coding & CAEL

## 1. Visão Geral: O Paradigma de Vibe Coding Governado

O **Vibe Coding** representa a transição da programação baseada em digitação manual de sintaxe para um modelo orientado a **expressão de intenção em linguagem natural mediada por agentes de Inteligência Artificial**. 

Para evitar a degradação estrutural e a acumulação acelerada de débito técnico, o Agents Squad adota o **Governed Vibe Coding** — uma abordagem onde a fluidez da IA é balizada por uma "cerca elétrica" determinística de arquitetura, testes e contratos.

```
       VIBE CODING PURO                            GOVERNED VIBE CODING
┌─────────────────────────────┐             ┌─────────────────────────────────┐
│ • Intenção informal         │             │ • Especificação de Contrato     │
│ • Geração cega de código    │   ──────►   │ • Geração em sandbox TDD        │
│ • "Se compilar, está bom"   │             │ • Verificação por linters/gates │
│ • Débito técnico invisível  │             │ • Rastreabilidade e Ledger      │
└─────────────────────────────┘             └─────────────────────────────────┘
```

---

## 2. O Papel do Desenvolvedor: Diretor de Intenção e Auditor de Restrições

No paradigma governado, o desenvolvedor atua em três níveis:
1. **Diretor de Intenção**: Articular problemas de negócio, objetivos de produto, critérios de valor e limites de domínio em linguagem clara e testável.
2. **Auditor de Restrições**: Estabelecer as barreiras que a IA não pode violar (proibição de novas dependências, reuso obrigatório de módulos locais, contratos de dados explícitos).
3. **Validador de Evidências**: Garantir que toda conclusão seja respaldada por logs reais de compilação, suítes de teste verdes e verificação de contratos de componentes.

---

## 3. O Ciclo de Vida Contínuo (Continuous AI-Engineering Lifecycle - CAEL)

O ciclo de vida CAEL é composto por cinco etapas sequenciais e retroalimentadas:

```text
[1. Intenção & Especificação de Contrato]
                   │
                   ▼
[2. Geração Dirigida por Testes (TDD)]
                   │
                   ▼
[3. Guarda Estática & Poda de Inchaço]
                   │
                   ▼
[4. Auditoria de Gates CI/CD & Segurança]
                   │
                   ▼
[5. Feedback Loop & Refinamento de Regras]
                   │
                   └───► (Retroalimentação contínua de regras)
```

### Fase 1: Intenção & Especificação de Contrato (*Intent Framing*)
- O desenvolvedor fornece o objetivo funcional em linguagem natural.
- O agente `requirements-analyst` e o `product-owner` convertem a intenção em `vibe-intent-spec.md` contendo:
  - User Story no formato INVEST.
  - Critérios de Aceite no formato BDD/Gherkin (`Dado / Quando / Então`).
  - Esquema de dados ou contrato de API tipado.

### Fase 2: Geração Dirigida por Testes (*Test-Driven AI Synthesis*)
- O agente de engenharia escreve primeiro os testes unitários e de integração baseados nos critérios BDD.
- Os testes falham comprovadamente (*Red*).
- A IA gera a implementação estritamente necessária para aprovar os testes (*Green*).

### Fase 3: Guarda Estática & Poda de Inchaço (*Static Guard & Bloat Trimming*)
- Execução de `python scripts/verify_clean_code.py`.
- Verificação de:
  - Presença obrigatória do bloco de **Contrato de Componente** e docstrings tipadas.
  - Eliminação de imports mortos ou dependências não catalogadas.
  - Complexidade cognitiva $\le 8$.

### Fase 4: Auditoria de Gates e Segurança (*Gate Verification & Review*)
- Os agentes `code-reviewer` e `security-reviewer` avaliam o código contra OWASP Top 10, ASVS 4.0 e Clean Code.
- Emissão da decisão formal de gate em `gate-decisions/GD-*.yaml`.
- Atualização do `docs/delivery-ledger.md` com hashes, testes e artefatos.

### Fase 5: Feedback Loop & Refinamento de Regras (*Rule Refinement*)
- Padrões de falha detectados ou novas diretrizes aprendidas são registrados como deltas de memória (`MEM-*.yaml`) e promovidos para as regras canônicas do projeto.

---

## 4. Diretrizes Anti-Bloat para Geração de Código por IA

1. **Constraint-First**: Sempre declare as restrições antes de solicitar o código (ex: *"sem novas dependências, usar helpers de core/utils"*).
2. **Edição Cirúrgica**: Nunca reescreva arquivos inteiros; use substituições pontuais (*diff-only*).
3. **Proibição de Wrappers Especulativos**: Não crie camadas de abstração ou factories para casos de uso únicos.
4. **Validação nas Bordas, Confiança nos Contratos**: Valide estritamente dados externos; confie em tipos e contratos no núcleo da aplicação.
