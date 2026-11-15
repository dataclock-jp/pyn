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


@pytest.mark.parametrize("source,target,title", DOCS)
def test_generated_markdown_says_where_it_comes_from(source, target, title):
    """「手で編集しない」は生成物の側に出す。開いた人が読むのはこちら。"""
    text = (ROOT / target).read_text(encoding="utf-8")
    assert f"このファイルは {source} から生成されています" in text.split("\n## ")[0]


@pytest.mark.parametrize("source,target,title", DOCS)
def test_source_documents_have_no_comments(source, target, title):
    """docs/*.pyn は patch で書き換えるので、`#` コメントは次の patch で消える(d-02)。

    以前は 1 行目のコメントを patch のたびに手で戻していた。残したいことは
    散文か属性に書くこと。
    """
    from pyn import comment_lines

    assert comment_lines((ROOT / source).read_text(encoding="utf-8")) == []
