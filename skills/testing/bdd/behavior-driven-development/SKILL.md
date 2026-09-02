---
name: behavior-driven-development
description: "Behavior-Driven Development (BDD) com Gherkin. Use when the user asks for BDD, Given/When/Then, acceptance criteria, Gherkin scenarios, feature files, behavior specifications, or wants to bridge business requirements and executable tests."
---

# Behavior-Driven Development (BDD)

## Quando usar

- Épicos/US com critérios de aceitação complexos ou cross-funcionais
- Necessário alinhar linguagem de negócio com critérios de teste executáveis
- Product Owner quer validar que cenários refletem requisitos antes da implementação
- Test Engineer precisa de especificação de comportamento antes de codificar testes

## Quando NÃO usar

- Tarefas puramente de configuração ou documentação sem comportamento
- Apenas refatoração interna sem mudança de comportamento visível
- Prototipação descartável sem critérios de aceite formais

## Formato canônico

Cada cenário BDD segue Given/When/Then:

```gherkin
Funcionalidade: <nome da funcionalidade>
  Como <ator>
  Quero <ação/valor>
  Para <benefício de negócio>

  Cenário: <comportamento esperado>
    Dado <estado inicial do sistema>
    Quando <ação do usuário/sistema>
    Então <resultado esperado>
```

### Regras do formato

- `Dado`: contexto/precondição (não descreve ações do usuário)
- `Quando`: ação que dispara o comportamento
- `Então`: resultado observável e verificável
- Usar linguagem onipresente (ubiquitous language) do domínio
- Um cenário = um comportamento; evitar múltiplos `E Então` em excesso

## Integração com work items

Caminho padrão:
```
work/<WORK-ID>/
  specification/
    bdd-scenarios/
      <feature-name>.feature
```

Nomenclatura:
- Arquivo: kebab-case
- Funcionalidade: título descritivo do domínio
- Cenário: comportamento específico verificável

## Relação com TDD

- **BDD define o quê**: comportamento esperado do ponto de vista de negócio
- **TDD define o como**: implementação passo a passo via Red-Green-Refactor
- Orem de execução: BDD primeiro (especificação) → TDD depois (implementação)
- Um cenário BDD pode gerar múltiplos testes de unidade via TDD

## Critérios de qualidade

- Cenários devem ser independentes (sem dependência de ordem)
- Cada `Então` deve ser verificável por teste automatizado
- Evitar detalhes de UI/tecnologia em cenários de negócio
- Scenario Outline quando o mesmo comportamento se repete com dados diferentes

```gherkin
Cenário: <nome>
  Dado que <dados>
  Quando <ação>
  Então <resultado>

  Esquema do Cenário: <nome>
    Dado que <dados>
    Quando <ação>
    Então <resultado>
```

## Anti-padrões

- `Dado` descreve ações (deve ser estado)
- `Quando` descreve resultado (deve ser ação)
- Cenários dependentes de ordem de execução
- Misturar regras de negócio com detalhes técnicos
- Especificar comportamento já coberto por testes existentes

## Domínios típicos

- User Stories de média/alta complexidade
- Regras de negócio cross-funcionais
- Critérios de aceitação de Épicos
- Contratos de comportamento entre serviços/modulos
