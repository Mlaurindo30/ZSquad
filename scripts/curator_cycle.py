"""Ciclo periodico de revisao de intake do squad (adaptacao do curator do Hermes).

O que e:
    Rotina de manutencao que fotografa o estado de
    ``skills/discovery/intake/``, submete cada skill aos gates existentes de
    ``scripts/skill_curator.review_intake_skill`` e produz um relatorio com
    recomendacoes (``promote-candidate``, ``fix-findings`` ou
    ``expire-candidate``). E uma adaptacao em somente-leitura do ciclo do
    Hermes (``agent/curator.py`` + ``agent/curator_backup.py``): Periodicidade
    com seguranca de backup antes de qualquer analise.

Responsabilidade:
    - Snapshot do intake em ``<backup_dir>/curator-backup-<UTCtimestamp>.json``
      com path relativo, mtime ISO e sha256 de cada SKILL.md.
    - Revisao de cada skill em intake pelos gates ja existentes (security,
      license, anti-fabricacao, linter Hermes).
    - Recomendacao por item: aprovado -> ``promote-candidate``; reprovado com
      idade (mtime do SKILL.md) estritamente maior que 7 dias ->
      ``expire-candidate``; reprovado dentro da retencao -> ``fix-findings``
      (regra de retencao de intake: 7 dias, per AGENTS.md secao 6).

Pra que serve:
    Dar ao squad um ciclo periodico e reproduzivel de triagem do intake sem
    conceder ao script qualquer poder de mutacao: promocao, expurgo e
    correcao continuam acoes governadas pela persona 18 (skill-curator).
    O relatorio e a unica saida; o backup JSON e a unica escrita em disco.

Comportamento em falha:
    Erros de SO (diretorio de backup inacessivel, disco, permissoes)
    propagam como ``OSError`` de ``run_cycle``; o CLI converte em
    ``CURATOR_ERROR <motivo>`` com codigo de saida 1. Intake ausente ou
    vazio nao e erro: produz ``items`` vazios, contadores zerados e mesmo
    assim grava o backup ``{}``.

Conexoes:
    - ``scripts/skill_curator.py`` — gates de curadoria (chamada real,
      sem bypass).
    - Persona ``18-skill-curator`` — consumidor do relatorio e unico
      autorizado a agir sobre as recomendacoes.
    - ``skills/discovery/intake/<skill-name>/SKILL.md`` — layout de entrada
      lido em ``<runtime_root>``.

Dependencias & Imports:
    Somente stdlib (``argparse``, ``hashlib``, ``json``, ``sys``, ``time``,
    ``datetime``, ``pathlib``). Nao usa rede, LLM, banco ou PyYAML.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

# Bootstrap de import direto (mesmo padrao de scripts/auto_skill_learner.py):
# permitir ``python scripts/curator_cycle.py`` alem de ``python -m``.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import skill_curator

#: Layout do intake dentro da raiz do runtime (AGENTS.md secao 1 e 6).
INTAKE_RELPATH = Path("skills") / "discovery" / "intake"

#: Retencao de intake em dias: itens mais antigos que isso e nao aprovados
#: sao candidatos a expiracao (AGENTS.md secao 6: "intake retention is 7 days").
RETENTION_DAYS = 7

_SECONDS_PER_DAY = 86400.0
_BACKUP_PREFIX = "curator-backup-"


def _utc_stamp(now: float) -> str:
    """Timestamp UTC filesystem-safe (ex.: ``20260822T143005Z``)."""
    return datetime.fromtimestamp(now, tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _discover_intake_skills(intake_root: Path) -> List[Tuple[str, Path]]:
    """Lista ``(nome, caminho_do_SKILL.md)`` de cada skill em intake.

    Skill em intake = subpasta de ``intake`` contendo ``SKILL.md``; arquivos
    soltos e pastas sem ``SKILL.md`` sao ignorados. Ausencia do diretorio
    ``intake`` devolve lista vazia (nao e erro).
    """
    if not intake_root.is_dir():
        return []
    skills: List[Tuple[str, Path]] = []
    for child in sorted(intake_root.iterdir()):
        skill_md = child / "SKILL.md"
        if child.is_dir() and skill_md.is_file():
            skills.append((child.name, skill_md))
    return skills


def _snapshot_intake(
    runtime_root: Path,
    backup_dir: Path,
    skills: List[Tuple[str, Path]],
    now: float,
) -> Path:
    """Grava o backup JSON do estado do intake ANTES de qualquer revisao.

    O que e:
        Foto do intake em ``backup_dir/curator-backup-<UTCtimestamp>.json``:
        ``{skill_name: {"path": <rel>, "mtime": <ISO UTC>, "sha256": <hex>}}``.
        Cria ``backup_dir`` se ausente; colisao de nome no mesmo segundo ganha
        sufixo ``-1``, ``-2``... (mesma tatica do ``curator_backup`` do Hermes).

    Responsabilidade:
        Garantir ponto de restauracao auditavel antes do ciclo recomendar
        qualquer acao sobre o intake.

    Pra que serve:
        Seguranca da manutencao periodica: qualquer item pode ser conferido
        contra hash/mtime do momento da analise.

    Comportamento em falha:
        Levanta ``OSError`` se o diretorio nao puder ser criado/escrito ou o
        SKILL.md nao puder ser lido; nao silencia falha de disco.

    Conexoes:
        Chamado por ``run_cycle`` (passo a); consumido por auditoria manual
        ou pela persona 18.

    Dependencias & Imports:
        stdlib (``json``, ``hashlib``, ``datetime``).
    """
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = _utc_stamp(now)
    path = backup_dir / f"{_BACKUP_PREFIX}{stamp}.json"
    suffix = 1
    while path.exists():
        path = backup_dir / f"{_BACKUP_PREFIX}{stamp}-{suffix}.json"
        suffix += 1

    payload = {}
    for name, skill_md in skills:
        stat = skill_md.stat()
        payload[name] = {
            "path": skill_md.relative_to(runtime_root).as_posix(),
            "mtime": datetime.fromtimestamp(
                stat.st_mtime, tz=timezone.utc).isoformat(),
            "sha256": hashlib.sha256(skill_md.read_bytes()).hexdigest(),
        }
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return path


def run_cycle(
    runtime_root: Path | str,
    backup_dir: Path | str,
    *,
    now: Optional[float] = None,
) -> dict:
    """Executa um ciclo completo de revisao de intake (backup + gates + relatorio).

    O que e:
        Passo periodico do squad: (a) fotografa o intake em backup JSON;
        (b) revisa cada skill com ``skill_curator.review_intake_skill``;
        (c) classifica por recomendacao e devolve o relatorio agregado.

    Responsabilidade:
        Produzir recomendacoes SEM executar acoes: nunca promove, exclui ou
        altera skills — a unica escrita e o JSON de backup dentro de
        ``backup_dir``; todo o resto e somente-leitura.

    Pra que serve:
        Insumo deterministico para a persona 18 decidir promocoes/expurgos
        com evidencia (hash, idade, blockers por gate).

    Comportamento em falha:
        ``OSError`` propaga (disco/permissoes). Gates falhaveis ja tratam seus
        proprios erros dentro de ``skill_curator``; ausencia de intake devolve
        ciclo vazio com backup gravado.

    Conexoes:
        ``scripts.skill_curator.review_intake_skill`` (gates reais);
        ``main`` (CLI); persona ``18-skill-curator`` (conselho e acao).

    Dependencias & Imports:
        stdlib + ``scripts.skill_curator``. Sem rede, sem LLM, sem banco.

    Args:
        runtime_root: raiz do runtime contendo ``skills/discovery/intake/``.
        backup_dir: diretorio de destino do backup (criado se ausente).
        now: epoch injectable para testes; default ``time.time()``.

    Returns:
        ``{"items": [{"name", "approved", "age_days", "recommendation",
        "blockers"}...], "backup_path": <str>,
        "counts": {"promote-candidate": n, "fix-findings": n,
        "expire-candidate": n}}``.
    """
    runtime_root = Path(runtime_root)
    backup_dir = Path(backup_dir)
    if now is None:
        now = time.time()

    skills = _discover_intake_skills(runtime_root / INTAKE_RELPATH)

    # (a) Backup primeiro: ponto de restauracao antes de qualquer analise.
    backup_path = _snapshot_intake(runtime_root, backup_dir, skills, now)

    # (b) Revisao item a item pelos gates existentes.
    items: List[dict] = []
    counts = {
        "promote-candidate": 0,
        "fix-findings": 0,
        "expire-candidate": 0,
    }
    for name, skill_md in skills:
        report = skill_curator.review_intake_skill(skill_md.parent)
        age_days = (now - skill_md.stat().st_mtime) / _SECONDS_PER_DAY
        if report.approved:
            recommendation = "promote-candidate"
        elif age_days > RETENTION_DAYS:
            recommendation = "expire-candidate"
        else:
            recommendation = "fix-findings"
        counts[recommendation] += 1
        items.append({
            "name": name,
            "approved": report.approved,
            "age_days": age_days,
            "recommendation": recommendation,
            "blockers": [
                finding.message
                for finding in report.findings
                if finding.severity == "BLOCKER"
            ],
        })

    # (c) Relatorio final.
    return {
        "items": items,
        "backup_path": str(backup_path),
        "counts": counts,
    }


def main(argv: Optional[List[str]] = None) -> int:
    """CLI do ciclo: ``--root`` e ``--backup-dir`` obrigatorios.

    O que e:
        Entrada de linha de comando que executa um ``run_cycle`` e resume o
        resultado em uma linha.

    Responsabilidade:
        Traduzir o relatorio em ``CURATOR_OK promote=<n> fix=<n> expire=<n>
        backup=<caminho>`` (exit 0) ou ``CURATOR_ERROR <motivo>`` (exit 1 em
        ``OSError``).

    Pra que serve:
        Agendamento periodico (tarefa/scheduler do squad) sem depender de
        interpretador interativo.

    Comportamento em falha:
        ``OSError`` durante o ciclo -> mensagem ``CURATOR_ERROR`` e exit 1;
        nenhum outro caminho muta estado.

    Conexoes:
        ``run_cycle``; agendador periodico do squad; persona 18 le o backup
        apontado por ``backup=``.

    Dependencias & Imports:
        stdlib (``argparse``, ``sys``).
    """
    parser = argparse.ArgumentParser(
        description=(
            "Ciclo de revisao de intake do squad: fotografa o intake, "
            "revisa cada skill nos gates existentes e recomenda (nunca age)."
        ),
    )
    parser.add_argument(
        "--root", required=True,
        help="Raiz do runtime contendo skills/discovery/intake/.")
    parser.add_argument(
        "--backup-dir", required=True,
        help="Diretorio onde o backup curator-backup-<ts>.json sera gravado.")
    args = parser.parse_args(argv)

    try:
        result = run_cycle(args.root, args.backup_dir)
    except OSError as exc:
        print(f"CURATOR_ERROR {exc}")
        return 1

    counts = result["counts"]
    print(
        f"CURATOR_OK promote={counts['promote-candidate']} "
        f"fix={counts['fix-findings']} expire={counts['expire-candidate']} "
        f"backup={result['backup_path']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
