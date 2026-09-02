"""Garante que os prompts dos providers nunca voltem a prescrever cópia integral do squad."""
from __future__ import annotations

from pathlib import Path

import scripts.validate_structure as subject


def test_copy_model_language_is_flagged(tmp_path: Path):
    (tmp_path / "AGENTS.md").write_text(
        "# Prompt\nCopie o squad inteiro para o projeto alvo.\n", encoding="utf-8"
    )
    (tmp_path / "CLAUDE.md").write_text("prompt limpo\n", encoding="utf-8")
    errors: list[str] = []
    subject._validate_prompt_copy_model(tmp_path, errors)
    assert len(errors) == 1
    assert errors[0].startswith("legacy copy-model reference in AGENTS.md")


def test_clean_prompts_and_missing_files_pass(tmp_path: Path):
    (tmp_path / "GEMINI.md").write_text("Use o runtime compartilhado central.\n", encoding="utf-8")
    errors: list[str] = []
    subject._validate_prompt_copy_model(tmp_path, errors)
    assert errors == []
