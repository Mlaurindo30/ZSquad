#!/usr/bin/env python3
import sys
from pathlib import Path
import yaml

def main():
    root = Path(__file__).resolve().parent.parent
    catalog_path = root / "config" / "skills-catalog.yaml"
    
    with open(catalog_path, "r", encoding="utf-8") as f:
        catalog_data = yaml.safe_load(f)
        
    catalog_entries = catalog_data.get("catalog", [])
    entry_by_path = {entry["path"]: entry for entry in catalog_entries if "path" in entry}
    
    # Reset or populate assignments from agents
    assignments = {path: set() for path in entry_by_path}
    
    for manifest_path in (root / "agents").rglob("manifest.yaml"):
        with open(manifest_path, "r", encoding="utf-8") as f:
            mdata = yaml.safe_load(f) or {}
        agent_id = mdata.get("agent")
        if not agent_id:
            continue
        for key in ("assigned", "native"):
            for skill in mdata.get(key, []):
                sp = skill.get("path")
                if sp in assignments:
                    assignments[sp].add(agent_id)
                
    for path, agent_set in assignments.items():
        entry = entry_by_path[path]
        current_assigned = set(entry.get("assigned_to", []))
        entry["assigned_to"] = sorted(list(agent_set))
        
    with open(catalog_path, "w", encoding="utf-8") as f:
        yaml.dump(catalog_data, f, allow_unicode=True, sort_keys=False)
        
    print("Successfully synchronized catalog assignments.")

if __name__ == "__main__":
    main()
