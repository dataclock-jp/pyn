"""t-13: Markdown 射影のテスト。

Markdown は HTML と同じく一方向の射影なので、往復ではなく次を検証する:

  * 見出しがノードの深さから作られる(q-03 が散文の見出しを禁じた理由)
  * ``@id`` が ID アンカー宛のリンクへ解決され、リンクテキストが自動補完される
  * コード領域の ``@id`` は書き換えない(s-02)
  * 散文の Markdown は解釈されず素通しする
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from pyn import parse, render_md

ROOT = Path(__file__).resolve().parents[1]

SRC = '''\
spec auth "認証システム仕様":
  status: draft
  depends: [a, b]
  task auth-01 "認可エンドポイント":
    est: 3d
    """
    | 列 | 値 |
    |---|---|
    | a | 1 |

    - 箇条書き
    """
  task auth-02 "トークン交換":
    """
    @auth-01 の後に着手する。記法は `@id` と書く。

    ```python
    x = "@auth-01"
    ```
    """
'''


def doc():
    return parse(SRC)


def test_document_title_and_headings():
    md = render_md(doc(), title="仕様")
    assert md.startswith("# 仕様\n")
    assert "## <a id=\"auth\"></a>認証システム仕様" in md
    assert "### <a id=\"auth-01\"></a>認可エンドポイント" in md


def test_type_and_id_are_visible():
    md = render_md(doc())
    assert "<sub>`spec` `auth`</sub>" in md


def test_attrs_become_bullets():
    md = render_md(doc())
    assert "- `status`: `draft`" in md
    assert "- `depends`: `a`, `b`" in md


def test_ref_resolves_to_anchor_link_with_title():
    md = render_md(doc())
    assert "[認可エンドポイント](#auth-01) の後に着手する" in md


def test_ref_inside_code_span_is_untouched():
    md = render_md(doc())
    assert "記法は `@id` と書く" in md


def test_ref_inside_fence_is_untouched():
    md = render_md(doc())
    assert 'x = "@auth-01"' in md


def test_prose_markdown_passes_through():
    md = render_md(doc())
    assert "| 列 | 値 |" in md
    assert "- 箇条書き" in md


def test_edges_are_not_duplicated_as_a_list():
    """refs は散文からの派生値なので、本文に既に同じリンクがある。"""
    md = render_md(doc())
    assert "関連:" not in md
    assert md.count("(#auth-01)") == 1


def test_dangling_ref_is_left_as_written():
    """未定義参照を黙って直さない。検出は validate_refs の仕事(cost-3 と同じ姿勢)。"""
    d = parse('note n1:\n  """\n  @nope を見よ。\n  """\n')
    assert "@nope を見よ。" in render_md(d)


def test_no_triple_blank_lines():
    md = render_md(doc(), title="仕様", subtitle="sub", note="生成物")
    assert "\n\n\n" not in md


def test_base_level_shifts_headings():
    md = render_md(doc(), base_level=1)
    assert "# <a id=\"auth\"></a>認証システム仕様" in md


def test_deep_nesting_falls_back_to_bold():
    src = "a n1:\n"
    for i in range(2, 9):
        src += " " * (2 * (i - 1)) + f"a n{i}:\n"
    md = render_md(parse(src), base_level=2)
    assert '<a id="n7"></a>**n7**' in md   # 見出しレベルを使い切った先
    assert "####### " not in md            # 7個の # は Markdown に存在しない


def test_every_node_appears():
    md = render_md(doc())
    for node_id in ("auth", "auth-01", "auth-02"):
        assert f'<a id="{node_id}"></a>' in md


def test_heading_levels_are_monotonic():
    md = render_md(doc())
    levels = [len(m) for m in re.findall(r"^(#+) <a id=", md, re.M)]
    assert levels == [2, 3, 3]


def test_cli_md(tmp_path):
    src = tmp_path / "doc.pyn"
    src.write_text(SRC, encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, "-m", "pyn", "md", str(src), "--title", "仕様"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.startswith("# 仕様")


# --------------------------------------------------------------------------
# q-03: 見出しはノード構造の専有物(Markdown 射影が成り立つ前提)
# --------------------------------------------------------------------------

def test_heading_in_prose_is_rejected():
    import pytest

    from pyn.errors import PynValidationError

    with pytest.raises(PynValidationError, match="見出し"):
        parse('note n1:\n  """\n  ## みだし\n  """\n')


def test_heading_inside_fence_is_allowed():
    d = parse('note n1:\n  """\n  ```\n  # これはコメント\n  ```\n  """\n')
    assert "# これはコメント" in render_md(d)
