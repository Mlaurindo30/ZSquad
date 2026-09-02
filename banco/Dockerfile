FROM python:3.11-slim

LABEL maintainer="Agents Squad Platform"
LABEL description="Container de persistência local SQLite/DuckDB e API de inteligência de código para o Agents Squad"

WORKDIR /app

# Instala dependências básicas de compilação e sqlite
RUN apt-get update && apt-get install -y --no-install-recommends \
    sqlite3 \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# Copia scripts e esquemas de banco
COPY banco/ /app/banco/
COPY scripts/ /app/scripts/

# Cria diretório de banco
RUN mkdir -p /app/banco

ENV PYTHONUNBUFFERED=1
ENV SQUAD_DB_PATH=/app/banco/squad.db

# Executa migração inicial do banco
RUN sqlite3 /app/banco/squad.db < /app/banco/schema.sql

EXPOSE 8080

CMD ["python", "-c", "import time; print('Agents Squad DB running'); time.sleep(31536000)"]
