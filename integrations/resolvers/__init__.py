from dataclasses import dataclass
import yaml

@dataclass
class ResolverContext:
    db_path: str
    config_dir: str

def load_yaml(path: str) -> dict:
    """
    Component Contract:
    - Definition: load_yaml utility function.
    - Responsibility: Safely loads a YAML file into a dictionary.
    - Purpose: Eliminates code duplication across resolvers.
    - Failure Behavior: Returns empty dict on Exception (File not found, invalid YAML).
    - Connections: File system.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}
