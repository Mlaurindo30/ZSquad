import ast
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

import pytest

from scripts.skill_discovery_ast import (
    DiscoveredSkill,
    DiscoveredTool,
    _file_key,
    _module_registers_tools,
    _parse_skill_frontmatter,
    discover_skills,
    discover_tools,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def sleep_ticks() -> None:
    time.sleep(0.01)


# ---------------------------------------------------------------------------
# _parse_skill_frontmatter
# ---------------------------------------------------------------------------


class TestParseSkillFrontmatter:
    def test_no_frontmatter(self):
        name, desc, meta = _parse_skill_frontmatter("hello world")
        assert name == ""
        assert desc == ""
        assert meta == {}

    def test_valid_frontmatter(self):
        content = "---\nname: my-skill\ndescription: Does things.\nauthor: me\n---\nbody"
        name, desc, meta = _parse_skill_frontmatter(content)
        assert name == "my-skill"
        assert desc == "Does things."
        assert meta == {"author": "me"}

    def test_strips_bom(self):
        content = "\ufeff---\nname: bom-skill\n---\nbody"
        name, desc, meta = _parse_skill_frontmatter(content)
        assert name == "bom-skill"

    def test_empty_yaml(self):
        content = "---\n---\nbody"
        name, desc, meta = _parse_skill_frontmatter(content)
        assert name == ""
        assert desc == ""


# ---------------------------------------------------------------------------
# _file_key
# ---------------------------------------------------------------------------


class TestFileKey:
    def test_same_content_same_key(self, tmp_path):
        f = tmp_path / "f.txt"
        f.write_text("hello", encoding="utf-8")
        k1 = _file_key(f)
        time.sleep(0.01)
        k2 = _file_key(f)
        assert k1 == k2

    def test_different_size_different_key(self, tmp_path):
        f = tmp_path / "f.txt"
        f.write_text("hello", encoding="utf-8")
        k1 = _file_key(f)
        f.write_text("hello world", encoding="utf-8")
        k2 = _file_key(f)
        assert k1 != k2


# ---------------------------------------------------------------------------
# _module_registers_tools
# ---------------------------------------------------------------------------


class TestModuleRegistersTools:
    def test_positive(self, tmp_path):
        mod = tmp_path / "tool.py"
        write(mod, "registry.register(name='x', schema={})\n")
        assert _module_registers_tools(mod) is True

    def test_negative(self, tmp_path):
        mod = tmp_path / "tool.py"
        write(mod, "print('no register here')\n")
        assert _module_registers_tools(mod) is False

    def test_missing_file(self, tmp_path):
        assert _module_registers_tools(tmp_path / "missing.py") is False

    def test_register_inside_function_is_ignored(self, tmp_path):
        mod = tmp_path / "tool.py"
        write(mod, "def f():\n    registry.register(name='x', schema={})\n")
        assert _module_registers_tools(mod) is False

    def test_syntax_error(self, tmp_path):
        mod = tmp_path / "tool.py"
        write(mod, "def f(:\n")
        assert _module_registers_tools(mod) is False


# ---------------------------------------------------------------------------
# discover_skills
# ---------------------------------------------------------------------------


class TestDiscoverSkills:
    def test_empty_dir(self, tmp_path):
        assert discover_skills(tmp_path) == []

    def test_discovers_skills(self, tmp_path):
        skills = tmp_path / "skills"
        write(skills / "alpha" / "SKILL.md", "---\nname: alpha\ndescription: A\n---\nbody")
        write(skills / "bravo" / "SKILL.md", "---\nname: bravo\ndescription: B\n---\nbody")
        result = discover_skills(skills)
        names = {s.name for s in result}
        assert names == {"alpha", "bravo"}

    def test_ignores_non_dir(self, tmp_path):
        skills = tmp_path / "skills"
        write(skills / "file.md", "---\nname: file\n---\n")
        result = discover_skills(skills)
        assert result == []

    def test_skips_dir_without_skill_md(self, tmp_path):
        skills = tmp_path / "skills"
        skills.mkdir(parents=True, exist_ok=True)
        (skills / "empty").mkdir()
        assert discover_skills(skills) == []

    def test_cache_hit(self, tmp_path):
        skills = tmp_path / "skills"
        write(skills / "alpha" / "SKILL.md", "---\nname: alpha\n---\nbody")
        first = discover_skills(skills)
        assert len(first) == 1
        # modify file after first scan
        time.sleep(0.05)
        write(skills / "alpha" / "SKILL.md", "---\nname: alpha\n---\nbody-v2")
        second = discover_skills(skills)
        assert len(second) == 1
        assert second[0].path.endswith("SKILL.md")

    def test_metadata_included(self, tmp_path):
        skills = tmp_path / "skills"
        write(skills / "alpha" / "SKILL.md", "---\nname: alpha\nauthor: me\ntags:\n  - a\n---\nbody")
        result = discover_skills(skills)
        assert result[0].metadata == {"author": "me", "tags": ["a"]}


# ---------------------------------------------------------------------------
# discover_tools
# ---------------------------------------------------------------------------


class TestDiscoverTools:
    def test_empty_dir(self, tmp_path):
        assert discover_tools(tmp_path) == []

    def test_discovers_tools(self, tmp_path):
        tools = tmp_path / "tools"
        write(tools / "alpha.py", "registry.register(name='alpha', schema={})\n")
        write(tools / "bravo.py", "registry.register(name='bravo', schema={})\n")
        result = discover_tools(tools)
        names = {t.name for t in result}
        assert names == {"alpha", "bravo"}

    def test_ignores_non_registering_module(self, tmp_path):
        tools = tmp_path / "tools"
        write(tools / "helper.py", "print('no register')\n")
        assert discover_tools(tools) == []

    def test_cache_hit(self, tmp_path):
        tools = tmp_path / "tools"
        write(tools / "alpha.py", "registry.register(name='alpha', schema={})\n")
        first = discover_tools(tools)
        assert len(first) == 1
        time.sleep(0.05)
        write(tools / "alpha.py", "registry.register(name='alpha', schema={})\n# comment\n")
        second = discover_tools(tools)
        assert len(second) == 1
