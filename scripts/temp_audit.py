import os
import glob
import re
import yaml

base_dir = r"C:\Users\miche\OneDrive\Documentos\agent_squad"
agents_dir = os.path.join(base_dir, "agents")

issues = []
summary = {
    "total_agents": 0,
    "valid_prompts": 0,
    "valid_manifests": 0,
    "valid_native_skills": 0,
    "valid_openai_yamls": 0,
    "total_issues": 0
}

agent_folders = [f for f in sorted(os.listdir(agents_dir)) if os.path.isdir(os.path.join(agents_dir, f)) and f != "_shared"]
summary["total_agents"] = len(agent_folders)

print(f"Auditing {len(agent_folders)} agent directories in: {agents_dir}\n")

# Check agent-registry.yaml if present
registry_path = os.path.join(base_dir, "config", "agent-registry.yaml")
registry_agents = {}
if os.path.exists(registry_path):
    with open(registry_path, "r", encoding="utf-8") as f:
        reg_data = yaml.safe_load(f)
        if isinstance(reg_data, dict) and "agents" in reg_data:
            registry_agents = reg_data["agents"]

for folder in agent_folders:
    folder_path = os.path.join(agents_dir, folder)
    prompt_path = os.path.join(folder_path, "PROMPT.md")
    manifest_path = os.path.join(folder_path, "skills", "manifest.yaml")
    
    agent_num_prefix = folder.split("-")[0]
    
    # 1. Inspect PROMPT.md
    agent_id_prompt = None
    if not os.path.exists(prompt_path):
        issues.append({"level": "ERROR", "agent": folder, "file": "PROMPT.md", "msg": "PROMPT.md file missing"})
    else:
        with open(prompt_path, "r", encoding="utf-8") as f:
            content = f.read()
        
        # Regex to find yaml block
        yaml_blocks = re.findall(r"```yaml\s*\n(.*?)\n```", content, re.DOTALL)
        if yaml_blocks:
            parsed = False
            for block in yaml_blocks:
                try:
                    data = yaml.safe_load(block)
                    if isinstance(data, dict) and "agent" in data:
                        if isinstance(data["agent"], dict) and "id" in data["agent"]:
                            agent_id_prompt = data["agent"]["id"]
                            parsed = True
                            summary["valid_prompts"] += 1
                            break
                        elif isinstance(data["agent"], str): # sometimes agent: name or string
                            agent_id_prompt = data["agent"]
                            parsed = True
                            summary["valid_prompts"] += 1
                            break
                except Exception as e:
                    issues.append({"level": "WARN", "agent": folder, "file": "PROMPT.md", "msg": f"YAML block syntax error: {e}"})
            if not parsed:
                issues.append({"level": "WARN", "agent": folder, "file": "PROMPT.md", "msg": "YAML block found but could not extract agent.id"})
        else:
            issues.append({"level": "WARN", "agent": folder, "file": "PROMPT.md", "msg": "No ```yaml block found"})

    # 2. Inspect skills/manifest.yaml
    manifest_agent_id = None
    if not os.path.exists(manifest_path):
        issues.append({"level": "ERROR", "agent": folder, "file": "skills/manifest.yaml", "msg": "manifest.yaml missing"})
    else:
        with open(manifest_path, "r", encoding="utf-8") as f:
            try:
                mdata = yaml.safe_load(f)
                if not isinstance(mdata, dict):
                    issues.append({"level": "ERROR", "agent": folder, "file": "skills/manifest.yaml", "msg": "manifest content is not a dict"})
                else:
                    summary["valid_manifests"] += 1
                    manifest_agent_id = mdata.get("agent")
                    if not manifest_agent_id:
                        issues.append({"level": "ERROR", "agent": folder, "file": "skills/manifest.yaml", "msg": "Missing 'agent' field"})
                    
                    # Native skills
                    native_list = mdata.get("native", [])
                    if isinstance(native_list, list):
                        for idx, n in enumerate(native_list):
                            if isinstance(n, dict):
                                rel_path = n.get("path")
                                if rel_path:
                                    full_p = os.path.join(base_dir, rel_path.replace("/", os.sep))
                                    if not os.path.exists(full_p):
                                        issues.append({"level": "ERROR", "agent": folder, "file": "skills/manifest.yaml", "msg": f"Native path does not exist: '{rel_path}'"})
                                else:
                                    issues.append({"level": "ERROR", "agent": folder, "file": "skills/manifest.yaml", "msg": f"Native entry [{idx}] missing 'path'"})
                    
                    # Assigned skills
                    assigned_list = mdata.get("assigned", [])
                    if isinstance(assigned_list, list):
                        for idx, a in enumerate(assigned_list):
                            if isinstance(a, dict):
                                rel_path = a.get("path")
                                if rel_path:
                                    full_p = os.path.join(base_dir, rel_path.replace("/", os.sep))
                                    if not os.path.exists(full_p):
                                        issues.append({"level": "ERROR", "agent": folder, "file": "skills/manifest.yaml", "msg": f"Assigned skill path does not exist: '{rel_path}'"})
                                else:
                                    issues.append({"level": "ERROR", "agent": folder, "file": "skills/manifest.yaml", "msg": f"Assigned entry [{idx}] missing 'path'"})
            except Exception as e:
                issues.append({"level": "ERROR", "agent": folder, "file": "skills/manifest.yaml", "msg": f"YAML syntax error: {e}"})

    # ID Mismatch checks
    if agent_id_prompt and manifest_agent_id and agent_id_prompt != manifest_agent_id:
        issues.append({"level": "WARN", "agent": folder, "file": "manifest vs prompt", "msg": f"ID Mismatch: PROMPT.md has '{agent_id_prompt}' but manifest.yaml has '{manifest_agent_id}'"})

    # Check registry match
    if manifest_agent_id and registry_agents:
        if manifest_agent_id not in registry_agents:
            issues.append({"level": "WARN", "agent": folder, "file": "agent-registry.yaml", "msg": f"Agent ID '{manifest_agent_id}' not found in config/agent-registry.yaml"})

    # 3. Inspect Native Skill Structure
    native_dir = os.path.join(folder_path, "skills", "native")
    if not os.path.exists(native_dir):
        issues.append({"level": "ERROR", "agent": folder, "file": "skills/native", "msg": "Directory skills/native does not exist"})
    else:
        subdirs = [d for d in os.listdir(native_dir) if os.path.isdir(os.path.join(native_dir, d))]
        if not subdirs:
            issues.append({"level": "ERROR", "agent": folder, "file": "skills/native", "msg": "No native skill subdirectory found"})
        for sd in subdirs:
            skill_md = os.path.join(native_dir, sd, "SKILL.md")
            openai_yaml = os.path.join(native_dir, sd, "agents", "openai.yaml")
            
            if not os.path.exists(skill_md):
                issues.append({"level": "ERROR", "agent": folder, "file": f"skills/native/{sd}/SKILL.md", "msg": "SKILL.md missing"})
            else:
                summary["valid_native_skills"] += 1
                with open(skill_md, "r", encoding="utf-8") as f:
                    sm_content = f.read()
                    if not sm_content.strip().startswith("---"):
                        issues.append({"level": "WARN", "agent": folder, "file": f"skills/native/{sd}/SKILL.md", "msg": "Missing frontmatter delimiters (---)"})

            if not os.path.exists(openai_yaml):
                issues.append({"level": "INFO", "agent": folder, "file": f"skills/native/{sd}/agents/openai.yaml", "msg": "openai.yaml missing"})
            else:
                summary["valid_openai_yamls"] += 1
                with open(openai_yaml, "r", encoding="utf-8") as f:
                    try:
                        oy_data = yaml.safe_load(f)
                        if not isinstance(oy_data, dict):
                            issues.append({"level": "WARN", "agent": folder, "file": f"skills/native/{sd}/agents/openai.yaml", "msg": "openai.yaml not a valid dictionary"})
                    except Exception as e:
                        issues.append({"level": "ERROR", "agent": folder, "file": f"skills/native/{sd}/agents/openai.yaml", "msg": f"YAML syntax error: {e}"})

summary["total_issues"] = len(issues)

print("=== AUDIT SUMMARY ===")
for k, v in summary.items():
    print(f"{k}: {v}")

print("\n=== AUDIT FINDINGS ===")
for i in issues:
    print(f"[{i['level']}] [{i['agent']}] ({i['file']}): {i['msg']}")
