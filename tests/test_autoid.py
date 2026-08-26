"""t-17: ID の自動採番と、ID 消失の検出。

cost-1(全ノードに一意な ID を振る規律)への対処は 2 段構えである。

  * 振る手間 → `python -m pyn id` が自動化する
  * **振った ID を変えない** → 自動化できないので、消えたら気づけるようにする

前者は「触ってよい範囲」が厳密に決まっていることが命で、後者は「見逃さない」ことが命。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from pyn import assign_ids, index_by_id, parse

ROOT = Path(__file__).resolve().parents[1]

DRAFT = '''\
# コメントは残る
spec auth "認証システム仕様":
  version: 1.2

  section "概要":
    """
    task "散文の中は宣言ではない":
    """
  task "認可エンドポイント":
    est: 3d
    note "既知の課題":
'''


def _run(*args):
    return subprocess.run([sys.executable, "-m", "pyn", *args],
                          cwd=ROOT, capture_output=True, text=True, encoding="utf-8")


# --------------------------------------------------------------------------
# 採番
# --------------------------------------------------------------------------

def test_assigns_ids_to_draft_declarations():
    text, assigned = assign_ids(DRAFT)
    assert [i for _, i in assigned] == ["auth.1", "auth.2", "auth.2.1"]
    assert 'section auth.1 "概要":' in text


def test_ids_follow_the_parent_hierarchy():
    """`repair.py` の命名ヒューリスティクスが生きるよう、親の ID を接頭辞にする。"""
    _, assigned = assign_ids(DRAFT)
    ids = [i for _, i in assigned]
    assert all(i.startswith("auth.") for i in ids)
    assert ids[-1].startswith("auth.2.")   # note は task の子


def test_root_level_uses_type_prefix():
    text, assigned = assign_ids('spec "ルート直下":\n')
    assert [i for _, i in assigned] == ["spec-1"]
    assert 'spec spec-1 "ルート直下":' in text


def test_existing_ids_are_never_touched():
    """ID の価値は安定していること。既存の ID は 1 バイトも変えない。"""
    source = 'spec auth "x":\n  task auth-01 "y":\n'
    text, assigned = assign_ids(source)
    assert text == source and assigned == []


def test_comments_and_blank_lines_survive():
    """正準形へ写して吐き直さないので、コメントも空行も残る(d-02 との整合)。"""
    text, _ = assign_ids(DRAFT)
    assert "# コメントは残る" in text
    assert "\n\n" in text


def test_prose_is_not_touched():
    text, _ = assign_ids(DRAFT)
    assert 'task "散文の中は宣言ではない":' in text


def test_attribute_lines_are_not_mistaken_for_declarations():
    text, assigned = assign_ids('task t1 "x":\n  est: 3d\n  owner: alice\n')
    assert assigned == []
    assert "est: 3d" in text


def test_result_parses():
    text, _ = assign_ids(DRAFT)
    doc = parse(text)
    assert set(index_by_id(doc)) >= {"auth", "auth.1", "auth.2", "auth.2.1"}


def test_is_idempotent():
    once, _ = assign_ids(DRAFT)
    twice, assigned = assign_ids(once)
    assert twice == once and assigned == []


def test_does_not_collide_with_existing_ids():
    source = 'spec auth "x":\n  task auth.1 "既にある":\n  task "新しい":\n'
    _, assigned = assign_ids(source)
    assert [i for _, i in assigned] == ["auth.2"]


def test_numbers_are_not_reused_after_deletion():
    """空き番号を埋め直さない(単調増加)。消えた ID が別の意味で復活するのを避ける。"""
    source = 'spec auth "x":\n  task auth.2 "2番だけ残っている":\n  task "新しい":\n'
    _, assigned = assign_ids(source)
    assert [i for _, i in assigned] == ["auth.3"]


def test_draft_requires_a_title():
    """`type:` だけの行は属性と紛らわしいので採番対象にしない。"""
    _, assigned = assign_ids("task t1 \"x\":\n  note:\n")
    assert assigned == []


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def test_cli_dry_run_does_not_write(tmp_path):
    src = tmp_path / "draft.pyn"
    src.write_text(DRAFT, encoding="utf-8")
    proc = _run("id", str(src), "--dry-run")
    assert proc.returncode == 0
    assert "auth.1" in proc.stderr
    assert src.read_text(encoding="utf-8") == DRAFT


def test_cli_writes_in_place(tmp_path):
    src = tmp_path / "draft.pyn"
    src.write_text(DRAFT, encoding="utf-8")
    assert _run("id", str(src)).returncode == 0
    assert "auth.1" in src.read_text(encoding="utf-8")
    assert _run("check", str(src)).returncode == 0


def test_cli_reports_nothing_to_do(tmp_path):
    src = tmp_path / "done.pyn"
    src.write_text('task t1 "x":\n', encoding="utf-8")
    proc = _run("id", str(src))
    assert proc.returncode == 0
    assert "採番するもの" in proc.stderr


def test_cli_rejects_input_that_cannot_parse(tmp_path):
    src = tmp_path / "broken.pyn"
    src.write_text('spec s1 "x":\n\ttask "タブ":\n', encoding="utf-8")
    assert _run("id", str(src)).returncode == 1


def test_repository_documents_need_no_assignment():
    """正本の .pyn はすべて ID 済みであること。"""
    for name in ("Handover.pyn", "docs/agents.pyn", "docs/prose-dialect.pyn",
                 "samples/auth-spec.pyn"):
        _, assigned = assign_ids((ROOT / name).read_text(encoding="utf-8"))
        assert assigned == [], name


# --------------------------------------------------------------------------
# ID 消失の検出
# --------------------------------------------------------------------------

def test_check_ids_passes_on_current_tree():
    from tools.check_ids import main

    assert main([]) == 0


def test_check_ids_detects_a_renumber(tmp_path, monkeypatch, capsys):
    """ID を付け替えたら気づくこと(採番より、こちらが cost-1 の本体)。"""
    import tools.check_ids as mod

    before = 'spec auth "x":\n  task auth.1 "y":\n'
    after = 'spec auth "x":\n  task auth.9 "y":\n'
    target = tmp_path / "doc.pyn"
    target.write_text(after, encoding="utf-8")

    monkeypatch.setattr(mod, "ROOT", tmp_path)
    monkeypatch.setattr(mod, "RETIRED", tmp_path / "retired-ids.txt")
    monkeypatch.setattr(mod, "git", lambda *a: (
        "ok" if a[0] == "rev-parse" else before if a[0] == "show" else ""))

    assert mod.main(["doc.pyn"]) == 1
    assert "auth.1" in capsys.readouterr().err


def test_retired_ids_file_allows_intentional_removal(tmp_path, monkeypatch):
    import tools.check_ids as mod

    before = 'spec auth "x":\n  task auth.1 "y":\n'
    target = tmp_path / "doc.pyn"
    target.write_text('spec auth "x":\n', encoding="utf-8")
    (tmp_path / "retired-ids.txt").write_text(
        "auth.1  # 統合したので廃止\n", encoding="utf-8")

    monkeypatch.setattr(mod, "ROOT", tmp_path)
    monkeypatch.setattr(mod, "RETIRED", tmp_path / "retired-ids.txt")
    monkeypatch.setattr(mod, "git", lambda *a: (
        "ok" if a[0] == "rev-parse" else before if a[0] == "show" else ""))

    assert mod.main(["doc.pyn"]) == 0


def test_retired_ids_file_exists_and_is_documented():
    text = (ROOT / "retired-ids.txt").read_text(encoding="utf-8")
    assert "rename" in text  # 改名は廃止ではない、と書いてあること


@pytest.mark.parametrize("rev", ["HEAD", "HEAD~1"])
def test_check_ids_accepts_a_base_revision(rev):
    from tools.check_ids import main

    assert main(["--base", rev]) == 0
