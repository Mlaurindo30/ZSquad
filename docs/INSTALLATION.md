# Agent Squad — Guia de Instalação e Configuração Zero-to-Hero

Este documento fornece o guia canônico e exaustivo de instalação, provisionamento e configuração do **Agent Squad** para desenvolvedores humanos e agentes autônomos, cobrindo todos os runtimes de IA suportados.

---

## 1. Visão Geral Zero-to-Hero

O **Agent Squad** é um framework governado de entrega de software com múltiplos agentes autônomos (41 especialistas), operando através de artefatos locais (`work/`), handoffs tipados, deltas de memória e gates estritos de verificação (G1–G6).

O processo Zero-to-Hero automatiza a criação do ambiente virtual, dependências Python, banco de dados SQLite (`banco/squad.db`), sincronização de servidores MCP, provisionamento de contêineres Docker e testes unitários.

---

## 2. Pré-requisitos de Sistema

Antes de iniciar a instalação, certifique-se de que sua máquina possui as seguintes ferramentas instaladas e acessíveis no `PATH`:

- **Python 3.11+** (`python --version`)
- **Node.js LTS** (`node --version` e `npm --version`) — Necessário para servidores MCP baseados em Node.
- **Git** (`git --version`)
- **Docker & Docker Compose** (Opcional, para serviços auxiliares como FalkorDB)
- **uv** (Opcional, para gerenciamento ultra-rápido de pacotes Python)

---

## 3. Auto-Bootstrap e Execução do Instalador

Para provisionar o ambiente completo em uma máquina limpa com um único comando:

### No Windows (PowerShell):
```powershell
.\install.ps1
```

### Em qualquer plataforma (Linux, macOS, Windows via Python direto):
```powershell
python scripts/setup_environment.py
```

O script executa 6 etapas automatizadas:
1. **Sincronização Upstream (`integrations/vendor/`)**: Atualiza submódulos e dependências de repositórios integrados.
2. **Ambiente Virtual Isolado (`.venv`)**: Cria o ambiente virtual (`.venv`) e instala os pacotes listados em `requirements.txt`.
3. **Banco de Dados SQLite (`banco/squad.db`)**: Inicializa o banco relacional executando o esquema canônico (`banco/schema.sql`).
4. **Servidores MCP (`config/mcp_config.json`)**: Configura e registra todos os servidores Model Control Protocol.
5. **Stack Docker**: Verifica se o daemon do Docker está ativo e sobe os serviços descritos em `docker-compose.yml`.
6. **Validação e Testes**: Executa o validador estrutural e a suíte de testes automatizados (`pytest`).

---

## 4. Instalação e Uso do CLI Global `squad`

Para disponibilizar o comando global `squad` no seu terminal (`PATH` do sistema):

```powershell
python -m pip install --editable .
```

Ou utilizando o script de inicialização do CLI:
- O executável será criado em `%USERPROFILE%\.agents_squad\bin\squad.cmd` ou via `console_scripts` do Python.

### Comandos principais do CLI:
- Inicializar um item de trabalho:
  ```powershell
  squad init-work-item --id EPIC-EXAMPLE --risk medium
  ```
- Executar diagnóstico do ambiente:
  ```powershell
  squad doctor
  ```
- Executar o motor contínuo de fluxo:
  ```powershell
  squad run-continuous --work-item EPIC-EXAMPLE
  ```

---

## 5. Configuração dos Provedores e Adaptadores de IA (Host Providers)

O Agent Squad integra-se nativamente com múltiplos ambientes e clientes de IA através de adaptadores dedicados.

### 5.1 Antigravity
Configure o caminho do MCP server no arquivo de configuração do Antigravity:
- **Caminho**: `~/.gemini/antigravity/mcp/mcp_config.json`
- **Conteúdo de Exemplo**:
  ```json
  {
    "mcpServers": {
      "agent-squad": {
        "command": "python",
        "args": ["-m", "integrations.mcp_runner"]
      }
    }
  }
  ```

### 5.2 Claude Desktop
Configure o Claude Desktop para carregar o Agent Squad MCP:
- **Caminho (Windows)**: `%APPDATA%\Claude\claude_desktop_config.json`
- **Conteúdo**:
  ```json
  {
    "mcpServers": {
      "agent-squad": {
        "command": "python",
        "args": ["-m", "integrations.mcp_runner"]
      }
    }
  }
  ```

### 5.3 VS Code / GitHub Copilot
Configure o arquivo de workspace do MCP:
- **Caminho**: `.vscode/mcp.json`
- **Conteúdo**:
  ```json
  {
    "servers": {
      "agent-squad": {
        "command": "python",
        "args": ["-m", "integrations.mcp_runner"]
      }
    }
  }
  ```

### 5.4 Cursor / Windsurf
Configure o editor Cursor:
- **Caminho**: `.cursor/mcp.json`
- **Conteúdo**:
  ```json
  {
    "mcp": {
      "agent-squad": {
        "command": "python",
        "args": ["-m", "integrations.mcp_runner"]
      }
    }
  }
  ```

### 5.5 Kilo Code
O Agent Squad inclui um adaptador dedicado (`integrations/kilo_adapter.py`) para configurar o Kilo Code idempotentemente via `configure_kilo_mcp`:
```python
from integrations.kilo_adapter import configure_kilo_mcp
configure_kilo_mcp(".kilo/kilo.jsonc", ".venv/Lib/site-packages")
```
Isso injeta a configuração no arquivo `kilo.jsonc` preservando comentários (JSONC) e apontando para `integrations.mcp_runner`.

### 5.6 ZCode (36 Subagentes Nativos)
Para provisionar os exatos 36 subagentes nativos do ZCode descritos em `config/zcode-agents.yaml`:
```powershell
python scripts/install_zcode_subagents.py --scope user --config config/zcode-agents.yaml
```
Para verificar o status sem aplicar mutações:
```powershell
python scripts/install_zcode_subagents.py --scope user --check
```

### 5.7 OpenClaw & Hermes
As integrações com OpenClaw e Hermes são gerenciadas através dos adaptadores especializados:
- **OpenClaw**: `integrations/openclaw_adapter.py`
- **Hermes**: `integrations/hermes_adapter.py`
Estes adaptadores mapeiam as sessões de agentes e canais de comunicação com o barramento do Agent Squad.

---

## 6. Diagnóstico e Autocura (Health Checks)

Se houver falhas de configuração ou inconsistências no ambiente, execute as ferramentas de diagnóstico:

1. **Validação Estrutural**:
   ```powershell
   python scripts/validate_structure.py
   ```
2. **Diagnóstico do Squad**:
   ```powershell
   squad doctor
   # ou
   python scripts/agent_squad.py doctor
   ```
3. **Suíte de Testes Automatizados**:
   ```powershell
   python -m pytest scripts/tests/ --cov=scripts --cov=integrations --cov-branch
   ```
