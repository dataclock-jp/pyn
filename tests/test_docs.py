"""`.pyn` から生成する Markdown 文書(AGENTS.md ほか)が追従していることの検証。

d-00 を CI 側から強制する仕組みは、Handover(tests/test_handover.py)と
schema(tests/test_schema.py)に続いてこれで 3 つめになる。
生成物を手で直しても、次のテストで必ず戻される。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pyn import parse
from tools.build_docs import DOCS, render

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("source,target,title", DOCS)
def test_generated_markdown_is_up_to_date(source, target, title):
    assert (ROOT / target).read_text(encoding="utf-8") == render(source, title), (
        f"{target} が {source} に追従していません —"
        " python -m tools.build_docs で再生成してください(d-00)"
    )


@pytest.mark.parametrize("source,target,title", DOCS)
def test_source_documents_are_valid(source, target, title):
    parse((ROOT / source).read_text(encoding="utf-8"))


def test_build_docs_check_mode():
    from tools.build_docs import main

    assert main(["--check"]) == 0


def test_agents_md_is_at_repository_root():
    """Codex CLI などが規約で読む位置に置くこと。"""
    assert (ROOT / "AGENTS.md").exists()


def test_claude_md_points_at_agents_md_without_duplicating_it():
    text = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    assert "AGENTS.md" in text
    assert len(text.splitlines()) < 15
