import json
import os
import tempfile
from pathlib import Path

CONFIG_PATH = os.path.expanduser("~/.gemini/config/mcp_config.json")

def main():
    # Ensure directory exists
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    
    # Load existing configuration
    data = {}
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            try:
                data = json.load(f)
            except json.JSONDecodeError:
                pass
                
    if "mcpServers" not in data:
        data["mcpServers"] = {}
        
    squad_runtime = os.environ.get("SQUAD_RUNTIME", str(Path(__file__).resolve().parents[1]))
    # Inject agent-squad (merge idempotente)
    data["mcpServers"]["agent-squad"] = {
        "command": "python",
        "args": ["-m", "integrations.mcp_runner"],
        "env": {
            "PYTHONPATH": squad_runtime,
            "PYTHONUNBUFFERED": "1"
        }
    }
    
    # Write to a temporary file in the same directory to ensure atomic replace
    fd, temp_path = tempfile.mkstemp(dir=os.path.dirname(CONFIG_PATH), text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.write("\n") # ensure trailing newline
            
        # Atomic replace
        os.replace(temp_path, CONFIG_PATH)
        print(f"Successfully registered agent-squad in {CONFIG_PATH}")
        print("Final JSON configuration:")
        print(json.dumps(data, indent=2))
    except Exception as e:
        os.remove(temp_path)
        print(f"Error occurred: {e}")
        raise

if __name__ == "__main__":
    main()
