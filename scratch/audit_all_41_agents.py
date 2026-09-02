import os
import re
from pathlib import Path
import yaml

ROOT = Path(r"c:\Users\miche\OneDrive\Documentos\agent_squad")
AGENTS_DIR = ROOT / "agents"
SHARED_DIR = AGENTS_DIR / "_shared"

registry_path = ROOT / "config/agent-registry.yaml"
with open(registry_path, "r", encoding="utf-8") as f:
    registry = yaml.safe_load(f)

expected_agents = registry.get("agents", [])
print(f"Total de agentes no registry: {len(expected_agents)}")

required_sections = [
    "ACTIVATION-NOTICE",
    "COMPLETE AGENT DEFINITION",
    "agent:",
    "persona_profile:",
    "persona:",
    "core_frameworks:",
    "core_principles:",
    "signature_vocabulary:",
    "commands:",
    "relationships:",
    "## Mission",
    "## Exclusive Responsibilities",
    "## Deliverables",
    "## Mandatory Protocol",
    "## Boundaries",
    "## Role Heuristics",
    "## When to Load Which Skill",
    "## How",
    "## Mandatory Handoff & Evidence Contract"
]

results = []

for entry in expected_agents:
    agent_id = entry["id"]
    agent_rel_path = entry["path"]
    agent_dir = ROOT / agent_rel_path
    
    prompt_file = agent_dir / "PROMPT.md"
    manifest_file = agent_dir / "skills/manifest.yaml"
    
    issues = []
    
    if not prompt_file.is_file():
        issues.append("PROMPT.md ausente")
        results.append((agent_id, False, issues, {}))
        continue
        
    prompt_text = prompt_file.read_text(encoding="utf-8")
    
    for section in required_sections:
        if section not in prompt_text:
            issues.append(f"Seção ausente: {section}")
            
    # Checar se ainda referencia ux-ui-designer em vez de ux-researcher / ui-designer
    if "ux-ui-designer" in prompt_text:
        issues.append("Referência obsoleta a 'ux-ui-designer' encontrada")
        
    # Verificar manifest
    if not manifest_file.is_file():
        issues.append("manifest.yaml ausente")
    else:
        try:
            m = yaml.safe_load(manifest_file.read_text(encoding="utf-8"))
            if not m.get("native"):
                issues.append("manifest sem campo 'native'")
        except Exception as e:
            issues.append(f"manifest inválido: {e}")
            
    # Extrair metadados para relatório
    meta = {
        "title": entry.get("title", ""),
        "lines": len(prompt_text.splitlines()),
        "has_works_with": "works_with:" in prompt_text,
    }
    
    results.append((agent_id, len(issues) == 0, issues, meta))

print("\n--- RESULTADO DA AUDITORIA DOS 41 AGENTES ---")
conformes = 0
for aid, ok, issues, meta in results:
    if ok:
        conformes += 1
        print(f"✅ {aid} ({meta['title']}) - {meta['lines']} linhas - 100% CONFORME")
    else:
        print(f"⚠️ {aid} ({meta.get('title', '')}) - {len(issues)} problemas:")
        for iss in issues:
            print(f"   - {iss}")

print(f"\nResumo: {conformes}/{len(results)} agentes 100% conformes com o novo padrão.")
