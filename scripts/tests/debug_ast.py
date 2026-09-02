import ast
from pathlib import Path

def _is_registry_register_call(node):
    if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
        return False
    func = node.value.func
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "register"
        and isinstance(func.value, ast.Name)
        and func.value.id == "registry"
    )

p = Path("C:/Users/miche/AppData/Local/Temp/test_reg.py")
p.write_text("registry.register(name='x', ...)\n", encoding="utf-8")
source = p.read_text(encoding="utf-8")
print("source:", repr(source))
print("contains registry:", "registry" in source)
print("contains register:", "register" in source)
tree = ast.parse(source, filename=str(p))
print("body:", tree.body)
print("result:", any(_is_registry_register_call(stmt) for stmt in tree.body))
