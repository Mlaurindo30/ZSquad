from dataclasses import dataclass


@dataclass
class ResolverContext:
    db_path: str
    config_dir: str
