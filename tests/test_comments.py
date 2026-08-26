"""t-14: コメントは正本ではない、を実装で固定する。

`#` は表層構文であり正準形に保存されない。したがって射影を再生成すれば必ず消える。
それ自体は s-meta からの帰結なので直さない。**黙って消さない**ことだけを保証する。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from pyn import comment_lines, parse, to_pyn_a

ROOT = Path(__file__).resolve().parents[1]

WITH_COMMENTS = '''\
# 先頭のコメント
task t1 "x":
  # 属性の理由
  est: 3d
  """
  ```
  # フェンス内のコメント
  ```
  #hashtag も本文
  """
'''


def _run(*args, cwd=ROOT):
    return subprocess.run([sys.executable, "-m", "pyn", *args],
                          cwd=cwd, capture_output=True, text=True, encoding="utf-8")


# --------------------------------------------------------------------------
# 検出 — 散文の中身を巻き込まないこと
# --------------------------------------------------------------------------

def test_detects_only_real_comments():
    found = comment_lines(WITH_COMMENTS)
    assert [n for n, _ in found] == [1, 3]


def test_prose_hash_lines_are_not_comments():
    found = comment_lines(WITH_COMMENTS)
    assert all("フェンス内" not in raw and "hashtag" not in raw for _, raw in found)


def test_no_comments_returns_empty():
    assert comment_lines('task t1:\n  est: 3d\n') == []


def test_crlf_and_bom_are_handled():
    assert comment_lines("﻿# c\r\ntask t1:\r\n") == [(1, "# c")]


def test_sample_document_has_comments():
    """サンプルは「コメントが書ける」ことの実例なので、あることを前提にしている。"""
    src = (ROOT / "samples" / "auth-spec.pyn").read_text(encoding="utf-8")
    assert comment_lines(src)


# --------------------------------------------------------------------------
# 実際に消えること(仕様どおりの挙動であることを固定する)
# --------------------------------------------------------------------------

def test_comments_are_dropped_by_projection():
    doc = parse(WITH_COMMENTS)
    assert comment_lines(to_pyn_a(doc)) == []


def test_prose_content_survives():
    """消えるのはコメントだけ。散文の # 行は本文なので残る。"""
    doc = parse(WITH_COMMENTS)
    assert "#hashtag も本文" in to_pyn_a(doc)


# --------------------------------------------------------------------------
# 黙って消さないこと
# --------------------------------------------------------------------------

def test_warns_on_projection_a(tmp_path):
    src = tmp_path / "doc.pyn"
    src.write_text(WITH_COMMENTS, encoding="utf-8")
    proc = _run("a", str(src))
    assert proc.returncode == 0
    assert "# コメント 2 行" in proc.stderr
    assert "line 1: # 先頭のコメント" in proc.stderr


def test_warns_on_projection_b_and_json(tmp_path):
    src = tmp_path / "doc.pyn"
    src.write_text(WITH_COMMENTS, encoding="utf-8")
    for action in ("b", "json"):
        assert "コメント" in _run(action, str(src)).stderr


def test_patch_warns_that_input_is_overwritten(tmp_path):
    src = tmp_path / "doc.pyn"
    src.write_text(WITH_COMMENTS, encoding="utf-8")
    ops = tmp_path / "ops.json"
    ops.write_text(json.dumps(
        [{"op": "set_attr", "id": "t1", "key": "est", "value": "5d"}]
    ), encoding="utf-8")

    proc = _run("patch", str(src), "--ops", str(ops))
    assert proc.returncode == 0
    assert "入力を上書きするので失われます" in proc.stderr
    assert comment_lines(src.read_text(encoding="utf-8")) == []


def test_patch_to_other_file_warns_without_overwrite_wording(tmp_path):
    src = tmp_path / "doc.pyn"
    src.write_text(WITH_COMMENTS, encoding="utf-8")
    ops = tmp_path / "ops.json"
    ops.write_text(json.dumps([]), encoding="utf-8")

    proc = _run("patch", str(src), "--ops", str(ops), "-o", str(tmp_path / "out.pyn"))
    assert proc.returncode == 0
    assert "出力には含まれません" in proc.stderr
    assert comment_lines(src.read_text(encoding="utf-8"))  # 原本は無傷


def test_no_warning_when_there_are_no_comments(tmp_path):
    src = tmp_path / "doc.pyn"
    src.write_text('task t1 "x":\n  est: 3d\n', encoding="utf-8")
    assert "コメント" not in _run("a", str(src)).stderr


def test_html_and_md_do_not_warn(tmp_path):
    """.pyn を置き換えない出力では、失われるものがないので黙っている。"""
    src = tmp_path / "doc.pyn"
    src.write_text(WITH_COMMENTS, encoding="utf-8")
    for action in ("html", "md"):
        assert "コメント" not in _run(action, str(src)).stderr


def test_many_comments_are_summarised(tmp_path):
    src = tmp_path / "doc.pyn"
    src.write_text("# a\n# b\n# c\n# d\n# e\ntask t1:\n", encoding="utf-8")
    stderr = _run("a", str(src)).stderr
    assert "# コメント 5 行" in stderr
    assert "他 2 行" in stderr


# --------------------------------------------------------------------------
# 文書側の主張が実態と合っていること
# --------------------------------------------------------------------------

def test_handover_no_longer_claims_comments_are_canonical():
    from pyn import index_by_id

    doc = parse((ROOT / "Handover.pyn").read_text(encoding="utf-8"))
    syntax = "\n".join(index_by_id(doc)["d-01.syntax"]["prose"])
    assert "正本に残せる" not in syntax
    assert "保存されない" in syntax
