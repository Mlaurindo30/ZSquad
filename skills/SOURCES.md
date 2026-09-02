# Fontes e atualização do parque de skills

Este arquivo é o único catálogo de links externos. As skills aprovadas são
executadas das cópias locais em `skills/`; os links servem para proveniência e
atualização controlada pelo Skill Curator.

## Baixadas e aprovadas

| Fonte | Uso local | Situação |
|---|---|---|
| [Databricks Agent Skills](https://github.com/databricks/databricks-agent-skills) | `data/databricks/` | Aprovada conforme licença incluída |
| [Sentry Skills](https://github.com/getsentry/skills) | `security/security-review` e `security/gha-security-review` | Aprovada; Apache-2.0 |
| [Superpowers](https://github.com/obra/superpowers) | `delivery/superpowers/` | Aprovada; MIT |
| [UI/UX Pro Max](https://github.com/nextlevelbuilder/ui-ux-pro-max-skill) | base de `engineering/ui-ux/` | Parte verificada MIT; complementos locais são uso interno |
| [Caveman](https://github.com/juliusbrussee/caveman) | `delivery/token-efficiency/caveman` | Aprovada como opt-in; MIT |
| [OWASP skill](https://github.com/agamm/claude-code-owasp) | `security/owasp-security` | Aprovada; MIT |

## Skills proprietárias e importações locais

Skills com `source: squad-internal` foram escritas para este time e são
proprietárias. Skills com `provenance: user-local-import` vieram das bibliotecas
locais fornecidas pelo proprietário; podem ser carregadas internamente, mas não
devem ser publicadas ou redistribuídas até a revisão individual de autoria e
licença. O catálogo não inventa uma origem externa para esses arquivos.

Os catálogos abaixo ajudam o Skill Curator a reencontrar e comparar candidatos,
mas não provam a origem de uma cópia local por similaridade:

- [Antigravity Skills](https://github.com/rmyndharis/antigravity-skills)
- [Agentic Awesome Skills](https://github.com/sickn33/agentic-awesome-skills)

## Avaliadas e rejeitadas

| Fonte | Local | Motivo |
|---|---|---|
| [LangChain Skills](https://github.com/langchain-ai/langchain-skills) | Conteúdo não mantido no projeto | Repositório sem licença identificada na revisão |

## Referências, não skills executáveis

| Fonte | Finalidade |
|---|---|
| [Awesome LLM Apps](https://github.com/Shubhamsaboo/awesome-llm-apps) | Exemplos para pesquisa de agentes/RAG; não foi copiado como skill |
| [skills.sh](https://skills.sh/) | Busca de candidatos; nenhum resultado entra em produção sem intake |

## Fontes candidatas para discovery futuro

- [Trail of Bits Skills](https://github.com/trailofbits/skills): avaliar
  compatibilidade CC-BY-SA antes de promover.
- [Anthropic Cybersecurity Skills](https://github.com/mukul975/Anthropic-Cybersecurity-Skills):
  importar apenas skills necessárias, nunca o repositório inteiro.

Atualizações são manuais, versionadas e passam por `config/discovery-policy.yaml`.
