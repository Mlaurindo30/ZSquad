---
name: security-review-gates
description: Executa revisão de segurança Sentry, GHA, OWASP e supply chain com threat model, evidência e decisão de gate.
---

# Security Review Gates

## When to Use
Use em mudanças de autenticação, autorização, dados sensíveis, prompts/ferramentas, dependências, CI/CD, Databricks, APIs ou exposição pública.

## When NOT to Use
Não substitui teste de intrusão autorizado nem aprovação humana para risco alto; não executar exploração destrutiva em produção.

## Procedimento
1. Delimite ativos, confiança, dados e ambiente; atualize threat model.
2. Rode a revisão Sentry `security-review` e, quando houver workflows, `gha-security-review`.
3. Complemente com OWASP Top 10/ASVS, MITRE ATT&CK/ATLAS, Trail of Bits (static analysis, insecure defaults, supply-chain-risk) e scanner de skills.
4. Classifique findings por impacto/probabilidade, reproduza de forma segura, indique correção e evidência.
5. Bloqueie G4 para crítico/alto sem correção ou waiver explícito; registre risco residual e expiração do waiver.

## Saída obrigatória
Threat model, security report, dependency/skill scan, GHA findings (se aplicável), decisão de gate e plano de remediação.

## Rationalizations to Reject
"É interno", "o workflow é só CI", "a skill é de fonte conhecida" e "não há dados reais" não são controles.
