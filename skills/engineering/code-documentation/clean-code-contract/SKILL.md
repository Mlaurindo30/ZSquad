---
name: clean-code-contract
description: Aplica código limpo, padrões consistentes e comentários de contrato com responsabilidade, falhas e conexões antes da revisão.
---

# Clean Code Contract

## When to Use
Use em toda implementação ou refatoração que altere comportamento, integrações, dados, prompts ou infraestrutura.

## When NOT to Use
Não use para renomear uma variável local óbvia ou alterar apenas formatação automática; ainda assim o lint deve passar.

## Procedimento
1. Escreva teste ou critério observável antes da mudança.
2. Mantenha uma responsabilidade por função/módulo, nomes de domínio, tipos explícitos, tratamento de erro contextual e dependências injetáveis.
3. Evite duplicação, abstrações prematuras, `utils` genérico e comentários que apenas repetem o código.
4. Em cada módulo, classe e função não trivial, use comentário de contrato no idioma do código:

```text
O que é: [componente e seu papel]
Responsabilidade: [única responsabilidade]
Pra que serve: [resultado esperado e consumidor]
Comportamento em falha: [erro, retry, fallback e observabilidade]
Conexões: [quem chama, o que chama, dados/eventos e arquivos relacionados]
```

5. Revise diff, testes, lint, segurança e documentação antes do handoff.

## Saída obrigatória
`code-diff`, testes executados, lista de conexões alteradas, falhas conhecidas e evidência de validação.

## Rationalizações to Reject
"É só um protótipo", "o nome explica tudo" e "documentamos depois" não dispensam o contrato quando o componente cruza fronteira de módulo, dados, API ou agente.
