# Threat Model — Template (STRIDE per Capability)

> Use STRIDE (Microsoft) para modelar ameaças por capability. Um arquivo por
> capability ou por área de produto. Data: YYYY-MM-DD. Owner: persona
> solution-architect (04) + security-reviewer (10). Atualizar a cada release.

## Capability: [name]

**Description**: [1-2 sentences. O que essa capability faz? Que dados toca?]

**Data classification**: [public | internal | confidential | restricted-PII]
**Trust boundaries**: [onde o dado cruza um limite de confiança?]
**Authentication**: [como o usuário é autenticado?]
**Authorization**: [qual o modelo de autorização? RBAC? ABAC?]
**Data flow**: [diagrama ASCII ou link para C4 Component]

```
[user] --> [API gateway] --> [service] --> [database]
                                |
                                v
                          [audit log]
```

## STRIDE per Element

| Element | S | T | R | I | D | E | Mitigation |
|---|---|---|---|---|---|---|---|
| [element 1] | spoofing | tampering | repudiation | info disclosure | DoS | EoP | [control] |
| [element 2] | ... |

## Threats (ranked by likelihood × impact)

| # | Threat | STRIDE | Likelihood | Impact | Risk | Mitigation | Status |
|---|---|---|---|---|---|---|---|
| 1 | [description] | T | High | High | Critical | [control] | Open/Closed |
| 2 | [description] | S | Medium | High | High | [control] | Open/Closed |

## Controls (mapped to ISO 27001 / SOC 2 / NIST)

| Control | Standard | Implementation | Verification |
|---|---|---|---|
| MFA em todos os acessos admin | ISO A.8.5, SOC 2 CC6.1 | Entra ID Conditional Access | `az ad policy list` |
| Encryption at rest | ISO A.8.24, SOC 2 CC6.7 | Storage Service Encryption / TDE | [test] |
| Audit log imutável | ISO A.8.15, SOC 2 CC7.2 | Log Analytics + immutable blob | [retention 400d] |

## Test Plan

* [SAST]: ferramenta + cadência
* [DAST]: ferramenta + cadência
* [Pen test]: frequência + escopo
* [Red Team]: `offensive-cyber-operator` (34) em `cyber_red@`

## Open Questions

* [Q1]
* [Q2]

## Change Log

| Date | Author | Change |
|---|---|---|
| YYYY-MM-DD | solution-architect | Initial |
