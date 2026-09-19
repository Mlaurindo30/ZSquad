# Banco de Dados & Infraestrutura do Agents Squad

Este diretório contém a infraestrutura de persistência, schemas relacionais/AST e orquestração Docker para o ecossistema do **Agents Squad**.

---

## Estrutura do Diretório

```text
banco/
├── squad.db            # Banco SQLite local embedded (WAL mode)
├── schema.sql          # Schema SQL canônico para símbolos AST, tokens e quórum
└── README.md           # Este guia de operação
```

---

## Modos de Execução

### 1. Execução Nativa (Local / Zero Dependências)
O banco SQLite opera de forma totalmente embedded através de `scripts/local_agent_db.py`, sem necessidade de daemons externos:
```powershell
python scripts/local_agent_db.py
```

### 2. Execução Containerizada via Docker Compose Canônico
A orquestração de containers unificada (Agents Squad Core + FalkorDB) é gerenciada a partir da raiz do projeto através de `docker-compose.yml`:
```powershell
docker compose up -d
```

### 3. Migração do Schema
Para recriar ou migrar as tabelas do banco:
```powershell
sqlite3 banco/squad.db < banco/schema.sql
```
