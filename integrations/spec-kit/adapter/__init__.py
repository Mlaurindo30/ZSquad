"""Adapter SDD do Spec Kit (T2) — contratos, carregamento e validação.

O diretório pai (``integrations/spec-kit``) contém hífen e não é
importável pelo nome; carregue este pacote via ``importlib``:

```python
import importlib.util
from pathlib import Path

base = Path(".../integrations/spec-kit/adapter")
spec = importlib.util.spec_from_file_location(
    "sdd_adapter", base / "__init__.py", submodule_search_locations=[str(base)]
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
```

Não altera ``sys.path`` global. Exporta a superfície mínima do contrato:
``load_package`` e ``validate_package``.
"""

from .contracts import load_package
from .validation import validate_package

__all__ = ["load_package", "validate_package"]
