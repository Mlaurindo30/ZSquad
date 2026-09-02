# 04 — Catálogo e Governança de Skills

## Tipos de Skills

1. **Native Skill**: A skill principal de cada persona, localizada em `agents/<NN>-<id>/skills/native/<id>-native/SKILL.md`. Carregada automaticamente com o perfil.
2. **Assigned Skills**: Skills locais mapeadas no `skills/manifest.yaml` da persona, carregadas sob demanda para tarefas específicas.
3. **Discovered Skills**: Skills externas obtidas sob curadoria rigorosa da persona `skill-curator`.
4. **Auto-Synthesized Skills**: Habilidades geradas autonomamente pelo motor `scripts/auto_skill_learner.py` a partir de soluções de work items bem-sucedidos.

## Pipeline de Autoaprendizagem (Inspirado em Hermes e Prime Agent)

```text
[ Work Item Entregue ]
        │
        ▼ (scripts/auto_skill_learner.py)
[ skills/discovery/intake/<skill>/SKILL.md ] (agentskills.io format)
        │
        ▼ (Quarentena & Testes de Segurança)
[ 18-skill-curator Review & Promotion ]
        │
        ▼ (Promotion)
[ skills/<domain>/<skill>/SKILL.md ] -> Atualiza config/skills-catalog.yaml
```

## Política de Uso e Orçamento

- **Limite de Contexto**: No máximo 7 skills carregadas por persona em uma sessão (sendo no máximo 3 descobertas).
- **Meta-Skill Obrigatória**: O orquestrador sempre carrega `using-superpowers` (`skills/delivery/superpowers/using-superpowers`) no bootstrap.
- **Segurança de Skills**: Skills fornecem métodos e procedimentos operacionais; **nunca** concedem credenciais, ferramentas ou autoridade de execução direta. Comandos contidos em skills são exemplos instrutivos.

