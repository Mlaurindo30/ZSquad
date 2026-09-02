#!/usr/bin/env python3
"""
O que é: Script auxiliar para criação de candidatos de intake de skills.
Responsabilidade: Criar diretórios e arquivos de intake em skills/discovery/intake e reviews.
Pra que serve: Apoiar o 18-skill-curator no processo de curadoria de skills externas.
Comportamento em falha: Emite mensagens de erro padrão de I/O caso o sistema de arquivos esteja bloqueado.
Conexões: Utilizado durante a curadoria de skills em skills/discovery/.
"""
import pathlib

CANDIDATES = [
    {
        "id": "CAVEMEM-001",
        "name": "cavemem",
        "repo": "JuliusBrussee/cavemem",
        "url": "https://github.com/JuliusBrussee/cavemem",
        "description": "Cross-agent persistent memory for coding assistants via SQLite + vector index.",
        "license": "MIT",
        "note": "Servidor MCP / Skill de memória persistente entre agentes"
    },
    {
        "id": "REQUESTING-CODE-REVIEW-001",
        "name": "requesting-code-review",
        "repo": "obra/superpowers",
        "url": "https://github.com/obra/superpowers",
        "description": "Superpowers skill para solicitação estruturada de revisão de código.",
        "license": "MIT",
        "note": "Mesma família de superpowers já ativas no squad"
    },
    {
        "id": "CODE-REVIEWER-GEMINI-001",
        "name": "code-reviewer-gemini",
        "repo": "google-gemini/gemini-cli",
        "url": "https://github.com/google-gemini/gemini-cli",
        "description": "Skill oficial de revisão de código do ecossistema Gemini CLI.",
        "license": "Apache-2.0",
        "note": "Revisão de código com foco em clareza e padrões"
    },
    {
        "id": "CODE-REVIEW-SECURITY-001",
        "name": "code-review-security",
        "repo": "hieutrtr/ai1-skills",
        "url": "https://github.com/hieutrtr/ai1-skills",
        "description": "Checklist de revisão de código baseado no OWASP Top 10.",
        "license": "MIT",
        "note": "Reforço de segurança para Code Reviewer e Security Reviewer"
    },
    {
        "id": "WRITING-SKILLS-001",
        "name": "writing-skills",
        "repo": "obra/superpowers",
        "url": "https://github.com/obra/superpowers",
        "description": "Superpowers skill para redação e estruturação de documentos técnicos.",
        "license": "MIT",
        "note": "Apoio ao Technical Writer"
    },
    {
        "id": "DOCUMENTATION-WRITER-001",
        "name": "documentation-writer",
        "repo": "github/awesome-copilot",
        "url": "https://github.com/github/awesome-copilot",
        "description": "Skill oficial do GitHub para escrita de documentação.",
        "license": "MIT",
        "note": "Apoio ao Technical Writer e padronização de docs"
    },
    {
        "id": "FIND-SKILLS-001",
        "name": "find-skills",
        "repo": "vercel-labs/skills",
        "url": "https://github.com/vercel-labs/skills",
        "description": "Ferramenta para busca e localização de skills por domínio.",
        "license": "MIT",
        "note": "Ferramental para apoio ao Skill Curator"
    },
    {
        "id": "SKILL-CREATOR-001",
        "name": "skill-creator",
        "repo": "anthropics/skills",
        "url": "https://github.com/anthropics/skills",
        "description": "Skill para criação e estruturação de novas skills no padrão SKILL.md.",
        "license": "MIT",
        "note": "Apoio ao Skill Curator no design de novas habilidades"
    },
    {
        "id": "GRILL-ME-001",
        "name": "grill-me",
        "repo": "JuliusBrussee/skills",
        "url": "https://github.com/JuliusBrussee/skills",
        "description": "Entrevista adversarial para questionar planos antes da construção.",
        "license": "MIT",
        "note": "Reforço de alinhamento e mitigação de risco nos gates G2/G3"
    },
    {
        "id": "JUNIOR-TO-SENIOR-001",
        "name": "junior-to-senior",
        "repo": "JuliusBrussee/skills",
        "url": "https://github.com/JuliusBrussee/skills",
        "description": "Revisão crítica e elevação de padrão de código.",
        "license": "MIT",
        "note": "Reforço de qualidade e revisão em G4"
    },
    {
        "id": "ANTIGRAVITY-AWESOME-SKILLS-001",
        "name": "antigravity-awesome-skills",
        "repo": "sickn33/antigravity-awesome-skills",
        "url": "https://github.com/sickn33/antigravity-awesome-skills",
        "description": "Coleção de skills para perfis de infraestrutura, banco de dados e performance.",
        "license": "MIT",
        "note": "Catálogo extenso - requer importação seletiva item a item"
    },
    {
        "id": "VERCEL-REACT-BEST-PRACTICES-001",
        "name": "vercel-react-best-practices",
        "repo": "vercel-labs/agent-skills",
        "url": "https://github.com/vercel-labs/agent-skills",
        "description": "Diretrizes de performance e arquitetura React da Vercel.",
        "license": "MIT",
        "note": "Especialização para Frontend Engineer"
    },
    {
        "id": "WEB-DESIGN-GUIDELINES-001",
        "name": "web-design-guidelines",
        "repo": "vercel-labs/agent-skills",
        "url": "https://github.com/vercel-labs/agent-skills",
        "description": "Diretrizes de acessibilidade e web design moderno.",
        "license": "MIT",
        "note": "Especialização para UX/UI Designer e Frontend Engineer"
    },
    {
        "id": "NODEJS-BACKEND-PATTERNS-001",
        "name": "nodejs-backend-patterns",
        "repo": "wshobson/agents",
        "url": "https://github.com/wshobson/agents",
        "description": "Padrões de backend e serviços Node.js.",
        "license": "MIT",
        "note": "Especialização para Backend Engineer"
    },
    {
        "id": "PYTHON-TYPE-SAFETY-001",
        "name": "python-type-safety",
        "repo": "wshobson/agents",
        "url": "https://github.com/wshobson/agents",
        "description": "Padrões de segurança de tipos e type hinting em Python.",
        "license": "MIT",
        "note": "Especialização para Backend, Software e Data Engineers"
    },
    {
        "id": "SCOPE-CREEP-DETECTOR-001",
        "name": "scope-creep-detector",
        "repo": "Shubhamsaboo/awesome-llm-apps",
        "url": "https://github.com/Shubhamsaboo/awesome-llm-apps",
        "description": "Detector de crescimento não planejado de escopo em diffs.",
        "license": "Apache-2.0",
        "note": "Reforço para Code Reviewer e Delivery Orchestrator"
    },
    {
        "id": "HERMES-SKILLS-001",
        "name": "hermes-skills",
        "repo": "aradotso/hermes-skills",
        "url": "https://github.com/aradotso/hermes-skills",
        "description": "Skills auto-geradas a partir de repositórios em alta.",
        "license": "Unknown",
        "note": "REQUER REVISÃO DE PROMPT-INJECTION ANTES DE QUALQUER INTAKE (skills auto-geradas a partir de repositórios em alta)"
    }
]

def main(root: pathlib.Path = pathlib.Path(".")) -> None:
    """Create candidate intake and review drafts below ``root``."""
    intake_dir = root / "skills/discovery/intake"
    reviews_dir = root / "skills/discovery/reviews"
    intake_dir.mkdir(parents=True, exist_ok=True)
    reviews_dir.mkdir(parents=True, exist_ok=True)

    for item in CANDIDATES:
        # 1. Intake folder and SKILL.md placeholder
        c_intake = intake_dir / item['name']
        c_intake.mkdir(parents=True, exist_ok=True)
        skill_content = f"""---
    name: {item['name']}
    description: {item['description']}
    license: {item['license']}
    metadata:
      status: draft_intake
      source_repo: {item['repo']}
      url: {item['url']}
      notes: "{item['note']}"
    ---

    # Intake Draft: {item['name']}

    - Repositório de Origem: {item['url']}
    - Status de Licença: {item['license']}
    - Observação de Avaliação: {item['note']}
    """
        (c_intake / "SKILL.md").write_text(skill_content, encoding="utf-8")

        # 2. Review record draft
        review_file = reviews_dir / f"SKILL-{item['id']}.md"
        review_content = f"""# SKILL-{item['id']}

    - Fonte: {item['url']} ({item['repo']})
    - Conteúdo avaliado: {item['description']}
    - Decisão: `draft_review` (pendente de validação formal pelo Skill Curator e aprovador humano).
    - Motivo: {item['note']}
    - Licença declarada: {item['license']}
    - Status de atribuição: Nenhuma skill promovida para `assigned` ou catálogo ativo sem due diligence completa de licença, checksum e segurança.
    """
        review_file.write_text(review_content, encoding="utf-8")

    print(f"INTAKE_AND_REVIEWS_CREATED count={len(CANDIDATES)}")


if __name__ == "__main__":
    main()
