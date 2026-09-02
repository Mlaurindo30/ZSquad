# Especificação de Intenção de Vibe Coding

## 1. Intenção do Usuário (Linguagem Natural)
[Descreva aqui o objetivo funcional de alto nível ou problema a ser resolvido]

## 2. História de Usuário (INVEST)
- **Título**: [ID-Título conciso]
- **Como** [papel/ator do sistema]
- **Eu quero** [funcionalidade/capacidade]
- **Para que** [valor de negócio/benefício gerado]

## 3. Critérios de Aceite (BDD / Gherkin)
```gherkin
Cenário: [Cenário principal de sucesso]
  Dado [estado inicial / pré-condição]
  Quando [ação executada pelo usuário/sistema]
  Então [resultado observável e efeito colateral esperado]

Cenário: [Tratamento de erro ou caso de borda]
  Dado [estado com entrada inválida ou falha de dependência]
  Quando [ação executada]
  Então [sistema deve falhar de forma segura e retornar erro específico]
```

## 4. Contrato de Dados / Interface
```json
{
  "request": {
    "type": "object",
    "required": ["id", "action"],
    "properties": {
      "id": { "type": "string" },
      "action": { "type": "string" }
    }
  }
}
```

## 5. Restrições Anti-Bloat Aplicáveis
- [ ] Nenhuma dependência externa adicional permitida.
- [ ] Reuso obrigatório de utilitários em `core/utils/`.
- [ ] Limite de complexidade ciclomática $\le 8$.
- [ ] Contrato de componente documentado no código gerado.
