"""Cenário controlado para popular ``ops_recovery`` com eventos reais.

Executa o ciclo "despacho → falha de provider → classificação → decisão →
persistência" ponta a ponta. **Sem rede e sem chamadas externas** — o
script apenas constrói ``ConnectionError`` em memória para simular um host
de provider offline, deixando o caminho de classificação, decisão e
persistência idêntico ao que o ``LLMRouter`` exercita em produção.

Política de segurança:

- Nenhuma URL é construída ou enviada; este script não faz requisições.
- Nenhum host real é tocado; nenhuma dependência de ``requests`` é usada.
- O simulador produz ``ConnectionError`` determinístico a partir de strings
  literais, para evitar SSRF, loopback, privado ou endereços reservados.

Uso::

    python scripts/run_recovery_simulation.py [--project-id alpha] [--db-path banco/squad.db]

Sai com código 0 quando pelo menos um evento foi persistido com binds
de parâmetros (sem concatenação de SQL).
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.error_classifier import classify_error
from scripts.local_agent_db import LocalAgentDB
from scripts.orchestration_controller import (
    FailureEvent,
    OrchestrationController,
)


# Marcadores descritivos (somente strings literais). Nenhuma URL.
_PROVIDERS = [
    ("omniroute", "agy/gemini-3.6-flash-low", "v1/chat/completions"),
    ("ollama-8b", "granite4.1:8b", "api/chat"),
    ("ollama-3b", "granite4.1:3b", "api/chat"),
]


def _simulate_connection_refused(provider: str, route: str) -> "requests.ConnectionError":
    """Constrói ``ConnectionError`` determinístico sem tocar em rede.

    Importação local para preservar a tipagem e a mensagem esperada pelo
    classificador; nenhum request é emitido.
    """
    import requests

    return requests.ConnectionError(
        f"Connection refused (simulado) para {provider} em {route}"
    )


def run_simulation(project_id: str, db_path: Path) -> int:
    db = LocalAgentDB(db_path, project_id=project_id)
    controller = OrchestrationController()
    controller.reset_attempts(project_id, "alpha/TASK-1")

    inserted: list[int] = []

    for provider, model, route in _PROVIDERS:
        exc = _simulate_connection_refused(provider, route)
        classified = classify_error(exc=exc, provider=provider)
        event = FailureEvent(
            project_id=project_id,
            work_item_id="TASK-1",
            target_key=f"alpha/TASK-1/dispatch/{provider}",
            phase="dispatch",
            agent_id="delivery-orchestrator",
            provider=provider,
            model=model,
            error_message=f"{type(exc).__name__}: {exc}",
            exc=exc,
            recorded_at=time.time(),
        )
        decision = controller.decide(event, fallback_agent_id="software-engineer")
        row_id = controller.record_event(db_path, decision, event)
        inserted.append(row_id)
        print(
            f"provider={provider} reason={classified.reason.value} "
            f"action={decision.action.value} row_id={row_id}"
        )

    for _ in range(3):
        exc = _simulate_connection_refused("omniroute", "v1/chat/completions")
        event = FailureEvent(
            project_id=project_id,
            work_item_id="TASK-1",
            target_key="alpha/TASK-1/dispatch/loop",
            phase="dispatch",
            agent_id="software-engineer",
            provider="omniroute",
            model="agy/gemini-3.6-flash-low",
            error_message=f"{type(exc).__name__}: {exc}",
            exc=exc,
            recorded_at=time.time(),
        )
        decision = controller.decide(event)
        row_id = controller.record_event(db_path, decision, event)
        inserted.append(row_id)
        print(
            f"loop reason={classify_error(exc=exc, provider='omniroute').reason.value} "
            f"action={decision.action.value} row_id={row_id}"
        )

    if not inserted:
        print("RECOVERY_SIMULATION_FAIL no_events_persisted")
        return 1

    conn = sqlite3.connect(str(db_path))
    cur = conn.execute(
        "SELECT COUNT(*) FROM ops_recovery WHERE project_id = ? AND recorded_at >= ?",
        (project_id, time.time() - 600),
    )
    recent_count = cur.fetchone()[0]
    sample = conn.execute(
        "SELECT target_key, reason, action, attempt, error_message "
        "FROM ops_recovery WHERE project_id = ? ORDER BY id DESC LIMIT 3",
        (project_id,),
    ).fetchall()
    conn.close()

    print(f"RECOVERY_SIMULATION_OK inserted={len(inserted)} recent_total={recent_count}")
    for row in sample:
        print("  sample", row)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="run_recovery_simulation",
        description="Cenário controlado para popular ops_recovery com eventos reais.",
    )
    parser.add_argument("--project-id", default="alpha")
    parser.add_argument(
        "--db-path",
        type=Path,
        default=ROOT / "banco" / "squad.db",
    )
    args = parser.parse_args(argv)
    args.db_path.parent.mkdir(parents=True, exist_ok=True)
    return run_simulation(args.project_id, args.db_path)


if __name__ == "__main__":
    raise SystemExit(main())
