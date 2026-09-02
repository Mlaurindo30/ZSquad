import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
import sys
sys.path.insert(0, str(ROOT / "scripts"))

from verify_clean_code import check_python_file, check_directory


class CleanCodeEnforcementTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root_path = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_compliant_file_passes(self):
        """A well-documented file with Component Contract and docstrings must pass cleanly."""
        code = '''"""
O que é: Módulo de autenticação de usuários.
Responsabilidade: Validar credenciais e emitir tokens JWT seguros.
Pra que serve: Proteger rotas autenticadas da aplicação.
Comportamento em falha: Retorna erro 401 para credenciais inválidas e loga tentativa.
Conexões: Consome tabela de usuários e emite eventos de login.
Dependências & Imports:
  - jwt: Geração e validação de tokens JWT.
"""

def authenticate_user(username: str, password_hash: str) -> bool:
    """Verifica as credenciais do usuário.

    Args:
        username: Identificador do usuário.
        password_hash: Hash da senha fornecida.

    Returns:
        bool: True se credenciais válidas, False caso contrário.
    """
    if not username or not password_hash:
        return False
    return True
'''
        file_path = self.root_path / "auth.py"
        file_path.write_text(code, encoding="utf-8")
        
        violations = check_python_file(file_path)
        self.assertEqual(violations, [])

    def test_missing_component_contract_detected(self):
        """A non-trivial file missing the component contract must be flagged."""
        lines = ["# Simple code without contract\n"] + [f"x_{i} = {i}\n" for i in range(25)]
        code = "".join(lines)
        
        file_path = self.root_path / "uncontracted.py"
        file_path.write_text(code, encoding="utf-8")
        
        violations = check_python_file(file_path)
        self.assertTrue(any(v.rule == "MISSING_COMPONENT_CONTRACT" for v in violations))

    def test_missing_function_docstring_detected(self):
        """A public non-trivial function without docstring must be flagged."""
        code = '''"""
O que é: Módulo de cálculo financeiro.
Responsabilidade: Calcular juros e amortização.
Pra que serve: Fornecer tabelas de parcelamento.
Comportamento em falha: Dispara ValueError para valores negativos.
Conexões: Consumido pelo endpoint de empréstimo.
"""

def calculate_compound_interest(principal: float, rate: float, periods: int) -> float:
    # Missing docstring!
    if principal <= 0 or rate <= 0:
        raise ValueError("Invalid parameters")
    total = principal * ((1 + rate) ** periods)
    return total
'''
        file_path = self.root_path / "finance.py"
        file_path.write_text(code, encoding="utf-8")
        
        violations = check_python_file(file_path)
        self.assertTrue(any(v.rule == "MISSING_DOCSTRING" for v in violations))


if __name__ == "__main__":
    unittest.main()
