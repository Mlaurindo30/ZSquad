# Contrato de Componente Governative

Use no comentário/docstring imediatamente no topo de cada módulo, classe ou componente não trivial:

```text
O que é: [tipo, nome e papel arquitetural]
Responsabilidade: [uma única responsabilidade verificável]
Pra que serve: [finalidade no fluxo de negócio, resultado e consumidor]
Comportamento em falha: [tratamento de erro, retry, fallback, logging e métricas]
Conexões: [chamadores, eventos emitidos/consumidos, tabelas, APIs e arquivos]
Dependências & Imports:
  - [módulo/lib]: [justificativa de uso e finalidade]
```

### Padrão de Docstrings em Funções e Métodos:
Toda função pública ou não trivial deve conter docstring tipada estruturada:

```python
def execute_action(param1: str, param2: int) -> ResultType:
    """Explicação concisa do objetivo e efeito colateral da função.

    Args:
        param1: Descrição e invariantes do parâmetro 1.
        param2: Descrição e limites do parâmetro 2.

    Returns:
        ResultType: Descrição do resultado retornado.

    Raises:
        SpecificError: Condição exata em que a exceção é disparada.
    """
```

