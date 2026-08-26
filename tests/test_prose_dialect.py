"""t-12: 散文 Markdown 方言の仕様(docs/prose-dialect.pyn)を実装で裏づける。

仕様書を書いただけでは、実装との食い違いは時間の問題で発生する。
d-00 と同じ姿勢で、**仕様書の主張そのものをテストにする**。
ここが落ちたときは、実装か仕様書のどちらかが嘘をついている。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from pyn import parse, render_html, render_md
from pyn.errors import PynValidationError

ROOT = Path(__file__).resolve().parents[1]
SPEC_PYN = ROOT / "docs" / "prose-dialect.pyn"
SPEC_MD = ROOT / "docs" / "prose-dialect.md"
SPEC_TITLE = "散文ブロックの Markdown 方言(PYN)"


def _prose(*lines: str) -> dict:
    body = "\n".join("  " + line if line else "" for line in lines)
    return parse(f'note n1:\n  """\n{body}\n  """\n')


def _html(*lines: str) -> str:
    return render_html(_prose(*lines))


def _md(*lines: str) -> str:
    return render_md(_prose(*lines))


# --------------------------------------------------------------------------
# 仕様書そのもの
# --------------------------------------------------------------------------

def test_spec_document_is_valid():
    proc = subprocess.run(
        [sys.executable, "-m", "pyn", "check", str(SPEC_PYN)],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
    )
    assert proc.returncode == 0, proc.stderr


def test_spec_markdown_is_up_to_date():
    doc = parse(SPEC_PYN.read_text(encoding="utf-8"))
    assert SPEC_MD.read_text(encoding="utf-8") == render_md(doc, title=SPEC_TITLE), (
        "docs/prose-dialect.md が古い —"
        " python -m pyn md docs/prose-dialect.pyn --title ... -o docs/prose-dialect.md"
    )


def test_spec_uses_child_nodes_instead_of_headings():
    """仕様書自身が「見出しを使いたくなったら子ノード」を実演していること。"""
    doc = parse(SPEC_PYN.read_text(encoding="utf-8"))
    assert len(doc["root"][0]["children"]) >= 5


# --------------------------------------------------------------------------
# 許可する記法(docs/prose-dialect.pyn の pd-allowed)
# --------------------------------------------------------------------------

def test_paragraph_is_split_by_blank_line():
    html = _html("一段落目。", "", "二段落目。")
    assert html.count("<p>") == 2


def test_unordered_list_markers():
    for marker in ("-", "*", "+"):
        assert "<ul><li>項目</li></ul>" in _html(f"{marker} 項目")


def test_ordered_list_is_renumbered_in_html_and_passed_through_in_md():
    html = _html("3. さん", "7. なな")
    assert "<ol><li>さん</li><li>なな</li></ol>" in html
    md = _md("3. さん", "7. なな")
    assert "3. さん" in md and "7. なな" in md


def test_nested_list_is_flattened():
    """仕様書の「ネストは非対応(平坦化される)」。"""
    html = _html("- 親", "  - 子")
    assert html.count("<ul>") == 1
    assert "<li>子</li>" in html


def test_blockquote():
    assert "<blockquote>" in _html("> 引用行")


def test_table_requires_separator_row():
    assert "<table>" in _html("| a | b |", "|---|---|", "| 1 | 2 |")
    # 区切り行がなければ、エラーではなくただの段落になる(pd-check の主張)
    html = _html("| a | b |")
    assert "<table>" not in html and "<p>" in html


def test_fence_both_styles_and_language():
    assert '<code class="lang-python">' in _html("```python", "x = 1", "```")
    assert "<pre><code>" in _html("~~~", "plain", "~~~")


def test_code_span_content_is_not_interpreted():
    html = _html("`**not bold** @notaref`")
    assert "<strong>" not in html
    assert "class=\"ref\"" not in html


def test_emphasis():
    html = _html("**太字** と *斜体*")
    assert "<strong>太字</strong>" in html and "<em>斜体</em>" in html


def test_link():
    assert '<a href="https://example.invalid/">表示</a>' in \
        _html("[表示](https://example.invalid/)")


# --------------------------------------------------------------------------
# 参照(pd-refs)
# --------------------------------------------------------------------------

def test_both_sigils_are_refs():
    doc = parse('note a:\n  """\n  @b と §b\n  """\nnote b "B":\n')
    assert doc["root"][0]["refs"] == ["b"]


def test_email_like_text_is_not_a_ref():
    doc = _prose("alice@example.invalid に連絡")
    assert "refs" not in doc["root"][0]


def test_refs_are_ordered_and_deduplicated():
    doc = parse('note a:\n  """\n  @c @b @c\n  """\nnote b:\nnote c:\n')
    assert doc["root"][0]["refs"] == ["c", "b"]


def test_refs_cannot_be_hand_written():
    """派生値。手で書いた refs は正準化で捨てられ、散文から再計算される。"""
    from pyn import canonicalize

    doc = canonicalize({"pyn": "1.0", "root": [
        {"id": "a", "type": "note", "refs": ["zzz"]},
    ]})
    assert "refs" not in doc["root"][0]


def test_ref_link_text_comes_from_target_title():
    doc = parse('note a:\n  """\n  @b を見よ\n  """\nnote b "参照先のタイトル":\n')
    assert '<a class="ref" href="#b">参照先のタイトル</a>' in render_html(doc)
    assert "[参照先のタイトル](#b)" in render_md(doc)


# --------------------------------------------------------------------------
# 文字の扱い(pd-text)
# --------------------------------------------------------------------------

def test_trailing_spaces_are_stripped():
    doc = _prose("行末に空白  ")
    assert doc["root"][0]["prose"][0] == "行末に空白"


def test_blank_lines_are_preserved_inside_but_trimmed_outside():
    doc = parse('note n1:\n  """\n\n  上\n\n  下\n\n  """\n')
    assert doc["root"][0]["prose"][0] == "上\n\n下"


def test_prose_is_dedented_by_opening_indent():
    doc = parse('note n1:\n  """\n  左\n    右\n  """\n')
    assert doc["root"][0]["prose"][0] == "左\n  右"


def test_prose_shallower_than_opening_is_an_error():
    from pyn.errors import PynIndentError

    with pytest.raises(PynIndentError):
        parse('spec s1:\n  note n1:\n    """\n  浅い\n    """\n')


def test_raw_html_is_escaped_in_html_and_passed_through_in_md():
    assert "&lt;b&gt;x&lt;/b&gt;" in _html("<b>x</b>")
    assert "<b>x</b>" in _md("<b>x</b>")


# --------------------------------------------------------------------------
# 禁止する記法(pd-forbidden)
# --------------------------------------------------------------------------

@pytest.mark.parametrize("line", ["# h1", "## h2", "###### h6"])
def test_headings_are_rejected(line):
    with pytest.raises(PynValidationError, match="見出し"):
        _prose(line)


def test_hash_without_space_is_not_a_heading():
    """`#tag` のような書き方まで巻き添えにしない。"""
    doc = _prose("#hashtag は見出しではない")
    assert doc["root"][0]["prose"][0].startswith("#hashtag")


def test_headings_inside_fence_are_allowed():
    doc = _prose("```", "# コメント", "```")
    assert "# コメント" in doc["root"][0]["prose"][0]


def test_lone_prose_delimiter_is_rejected():
    from pyn import canonicalize

    with pytest.raises(PynValidationError, match='"""'):
        canonicalize({"pyn": "1.0", "root": [
            {"id": "a", "type": "note", "prose": ['前\n"""\n後']},
        ]})
