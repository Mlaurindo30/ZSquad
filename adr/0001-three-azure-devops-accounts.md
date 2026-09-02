# ADR-0001 — Três contas Azure DevOps principais + duas service accounts

* Status: accepted
* Date: 2026-09-02
* Deciders: delivery-orchestrator (00), governance-auditor (14), product-owner (02)
* Consulted: security-reviewer (10), offensive-cyber-operator (34)

## Context and Problem Statement

O squad tem 41 personas operando sobre um único Azure DevOps. Para satisfazer
ISO/IEC 27001:2022 A.5.3 (segregation of duties) + SOC 2 TSC CC6.1 (logical
access segregation) + NIST SP 800-53 CM-5 (access restrictions for change),
precisamos separar contas AAD de modo que o **autor** nunca vote em sua
própria PR e o **revisor** não seja o mesmo agente que o **aprovador**. Sem
essa separação, perdemos o controle de auditoria e violamos o duplo sign-off
em paths sensíveis.

## Decision Drivers

* 41 personas precisam compartilhar 1-2 contas AAD (evitar e-mail por agente).
* Personas de PR (code-reviewer, security-reviewer, qa-engineer,
  performance-engineer, offensive-cyber-operator) precisam votar em paths
  distintos sem que o voto do autor conte.
* `offensive-cyber-operator` precisa de **duplo sign-off** com
  `security-reviewer` em `auth/`, `crypto/`, `iac/` — força conta dedicada.
* Acesso a dados sensíveis (PII) precisa de segregação por projeto.

## Considered Options

1. **1 conta única** (`squads@`) — todas as personas.
2. **3 contas principais** (`squads@`, `arthemis@`, `human_master`) — modelo
   SoD-compliant.
3. **3 contas principais + 2 service accounts** (`cyber_red@`,
   `customer_data_pii@`).

## Decision Outcome

Chosen option: **3 (3+2)**, porque separa o **default reviewer** (arthemis@)
do **author** (squads@) e isola o Red Team (cyber_red@) do voto regular.
Garante SoD AAD-level: `squads@` ≠ `arthemis@` ≠ `cyber_red@`. O
`customer_data_pii@` é somente-leitura e segregado por projeto.

### Consequences

* Good, because três níveis AAD distintos = 3 linhas de auditoria claras.
* Good, because duplo sign-off cross-account entre cyber_red@ e arthemis@
  é verificável em `/_apis/policy/evaluations`.
* Bad, because 1 conta extra para configurar no Azure DevOps
  (cyber-red@) e 1 para PII (customer_data_pii@).
* Bad, because o **time** precisa entender que 34-offensive-cyber-operator
  é resolvido por path filter, não por voto aberto.

### Confirmation

* `python -c "import yaml; t=yaml.safe_load(open('templates/devops.yaml'));
  dev=set(t['identities']['development_team']['used_by']);
  appr=set(t['identities']['pr_and_card_approver']['used_by']);
  sa=set(); [sa.update(v.get('used_by',[])) for v in t['service_accounts'].values()];
  print(len(dev|appr|sa))"` → 41
* `python -m pytest scripts/tests/test_azure_devops_project_setup.py` → 11/11
* 0/41 personas em duas service accounts (cada persona em exatamente 1 conta)
* `service_accounts.customer_data_pii.used_by == []` — sem persona vota com
  essa conta (segregação por projeto, somente leitura em PII).

## Pros and Cons of the Options

### 1 conta única

* Good, because trivial de configurar.
* Bad, because autor = revisor = aprovador. Sem SoD. Falha ISO 27001 A.5.3.

### 3 contas principais

* Good, because separa autor (squads@) de aprovador (arthemis@).
* Bad, because offensive-cyber-operator e security-reviewer no mesmo
  arthemis@ enfraquece o duplo sign-off (mesma conta vota em ambos).

### 3 contas principais + 2 service accounts (escolhido)

* Good, because duplo sign-off cross-account é verificável.
* Good, because customer_data_pii@ segregado por projeto e sem voto.
* Bad, because 2 contas AAD extras para provisionar.

## More Information

* `templates/devops.yaml:identities` e `:service_accounts`
* `agents/_shared/OPERATING_CONTRACT.md §"Quem aprova o quê (US-17)"`
* ISO/IEC 27001:2022 A.5.3, A.8.28, A.8.32
* SOC 2 TSC CC6.1, CC8.1
* NIST SP 800-53 CM-5
