FROM python:3.11-slim

LABEL maintainer="Agents Squad Platform"
LABEL description="Ambiente de Execução e Inteligência de Código para o Agents Squad"

WORKDIR /app

# Instala dependências do sistema operacional
RUN apt-get update && apt-get install -y --no-install-recommends \
    sqlite3 \
    curl \
    git \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copia dependências e instala
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# Copia estrutura do squad
COPY . /app/

ENV PYTHONUNBUFFERED=1
ENV SQUAD_RUNTIME=/app
ENV SQUAD_DB_PATH=/app/banco/squad.db

# Usuário não-privilegiado para execução segura do container
RUN useradd -m -u 1000 squaduser && chown -R squaduser:squaduser /app
USER squaduser

CMD ["python", "scripts/agent_squad.py", "audit"]
