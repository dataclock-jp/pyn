"""t-11: 投影B へのパッチ適用 API のテスト。

検証したいのは「関数が動く」ことではなく、s-01 の主張が実際に成立していること:

  * パッチの宛先は行番号ではなく ID である(テキストがずれても当たる)
  * 適用結果は必ず正準形であり、そこから投影A/B が再生成できる
  * 派生値(refs)はパッチが手で触らなくても正しく再計算される
  * 失敗したパッチは文書を壊さない(全か無か)
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from pyn import canonicalize, parse, to_pyn_a, to_pyn_b, validate_refs
from pyn.errors import PynValidationError
from pyn import patch

ROOT = Path(__file__).resolve().parents[1]

SRC = '''\
spec auth "認証システム仕様":
  task auth-01 "認可エンドポイント":
    est: 3d
    status: todo
  task auth-02 "トークン交換":
    est: 2d
    """
    @auth-01 の後に着手する。
    """
'''


@pytest.fixture
def doc():
    return parse(SRC)


# --------------------------------------------------------------------------
# 純粋性 — パッチは入力を壊さない
# --------------------------------------------------------------------------

def test_operations_do_not_mutate_input(doc):
    before = json.dumps(doc, ensure_ascii=False, sort_keys=True)
    patch.set_attr(doc, "auth-01", "status", "done")
    patch.remove(doc, "auth-02")
    patch.move(doc, "auth-02", None)
    assert json.dumps(doc, ensure_ascii=False, sort_keys=True) == before


def test_result_is_canonical(doc):
    out = patch.set_attr(doc, "auth-01", "status", "done")
    assert canonicalize(out) == out


def test_result_roundtrips_through_both_projections(doc):
    out = patch.set_attr(doc, "auth-01", "owner", "alice")
    assert parse(to_pyn_a(out)) == out
    assert parse(to_pyn_b(out)) == out


# --------------------------------------------------------------------------
# 属性・タイトル
# --------------------------------------------------------------------------

def test_set_attr_adds_and_overwrites(doc):
    out = patch.set_attr(doc, "auth-01", "status", "done")
    node = {n["id"]: n for n in out["root"][0]["children"]}["auth-01"]
    assert node["attrs"]["status"] == "done"
    assert node["attrs"]["est"] == "3d"  # 他の属性は残る


def test_set_attr_keeps_attrs_sorted(doc):
    out = patch.set_attr(doc, "auth-01", "aaa", 1)
    node = out["root"][0]["children"][0]
    assert list(node["attrs"]) == sorted(node["attrs"])


def test_set_attr_accepts_list_value(doc):
    out = patch.set_attr(doc, "auth-02", "depends", ["auth-01"])
    assert out["root"][0]["children"][1]["attrs"]["depends"] == ["auth-01"]


def test_set_attr_rejects_nested_structure(doc):
    with pytest.raises(PynValidationError):
        patch.set_attr(doc, "auth-01", "meta", {"k": "v"})


def test_del_attr(doc):
    out = patch.del_attr(doc, "auth-01", "status")
    assert "status" not in out["root"][0]["children"][0]["attrs"]


def test_del_attr_missing_raises_unless_allowed(doc):
    with pytest.raises(PynValidationError):
        patch.del_attr(doc, "auth-01", "nope")
    assert patch.del_attr(doc, "auth-01", "nope", missing_ok=True) == canonicalize(doc)


def test_set_title_and_clear(doc):
    out = patch.set_title(doc, "auth-01", "認可EP")
    assert out["root"][0]["children"][0]["title"] == "認可EP"
    assert "title" not in patch.set_title(out, "auth-01", None)["root"][0]["children"][0]


def test_unknown_id_is_rejected(doc):
    with pytest.raises(PynValidationError, match="見つかりません"):
        patch.set_attr(doc, "nope", "k", "v")


# --------------------------------------------------------------------------
# 散文と派生値(refs)
# --------------------------------------------------------------------------

def test_set_prose_recomputes_refs(doc):
    out = patch.set_prose(doc, "auth-01", "@auth-02 を待つ。")
    node = out["root"][0]["children"][0]
    assert node["refs"] == ["auth-02"]


def test_set_prose_drops_stale_refs(doc):
    out = patch.set_prose(doc, "auth-02", "参照のない文。")
    assert "refs" not in out["root"][0]["children"][1]


def test_add_prose_appends(doc):
    out = patch.add_prose(doc, "auth-01", "追記。")
    assert out["root"][0]["children"][0]["prose"] == ["追記。"]


def test_prose_in_code_span_is_not_an_edge(doc):
    out = patch.set_prose(doc, "auth-01", "記法は `@id` と書く。")
    assert "refs" not in out["root"][0]["children"][0]


# --------------------------------------------------------------------------
# 追加・削除・移動
# --------------------------------------------------------------------------

def test_add_under_parent(doc):
    out = patch.add(doc, {"id": "auth-03", "type": "task", "title": "失効"}, "auth")
    assert [c["id"] for c in out["root"][0]["children"]] == \
        ["auth-01", "auth-02", "auth-03"]


def test_add_at_index(doc):
    out = patch.add(doc, {"id": "auth-00", "type": "task"}, "auth", index=0)
    assert out["root"][0]["children"][0]["id"] == "auth-00"


def test_add_to_root(doc):
    out = patch.add(doc, {"id": "notes", "type": "note"})
    assert [n["id"] for n in out["root"]] == ["auth", "notes"]


def test_add_rejects_duplicate_id(doc):
    with pytest.raises(PynValidationError, match="既に存在"):
        patch.add(doc, {"id": "auth-01", "type": "task"}, "auth")


def test_add_rejects_duplicate_id_in_subtree(doc):
    subtree = {"id": "grp", "type": "group",
               "children": [{"id": "auth-02", "type": "task"}]}
    with pytest.raises(PynValidationError, match="既に存在"):
        patch.add(doc, subtree, "auth")


def test_remove_takes_subtree(doc):
    out = patch.remove(doc, "auth")
    assert out["root"] == []


def test_remove_promoting_children(doc):
    out = patch.remove(doc, "auth", with_children=False)
    assert [n["id"] for n in out["root"]] == ["auth-01", "auth-02"]


def test_remove_leaves_dangling_ref_visible(doc):
    """参照は黙って消さない。壊れたことが検出できる状態で残すのが正しい(cost-3)。"""
    out = patch.remove(doc, "auth-01")
    with pytest.raises(PynValidationError):
        validate_refs(out)


def test_move_to_root(doc):
    out = patch.move(doc, "auth-02", None)
    assert [n["id"] for n in out["root"]] == ["auth", "auth-02"]
    assert [c["id"] for c in out["root"][0]["children"]] == ["auth-01"]


def test_move_under_sibling(doc):
    out = patch.move(doc, "auth-02", "auth-01")
    assert out["root"][0]["children"][0]["children"][0]["id"] == "auth-02"


def test_move_rejects_cycle(doc):
    with pytest.raises(PynValidationError, match="循環"):
        patch.move(doc, "auth", "auth-01")


def test_move_preserves_subtree(doc):
    doc = patch.add(doc, {"id": "auth-02a", "type": "note"}, "auth-02")
    out = patch.move(doc, "auth-02", None)
    assert out["root"][1]["children"][0]["id"] == "auth-02a"


# --------------------------------------------------------------------------
# rename — 頂点の改名は辺の張り替えでもある
# --------------------------------------------------------------------------

def test_rename_updates_id_and_refs(doc):
    out = patch.rename(doc, "auth-01", "auth-authz")
    assert out["root"][0]["children"][0]["id"] == "auth-authz"
    assert out["root"][0]["children"][1]["refs"] == ["auth-authz"]
    assert "@auth-authz" in out["root"][0]["children"][1]["prose"][0]
    validate_refs(out)


def test_rename_does_not_touch_code_spans(doc):
    doc = patch.set_prose(doc, "auth-01", "`@auth-02` は記法の例。@auth-02 は参照。")
    out = patch.rename(doc, "auth-02", "auth-x")
    prose = out["root"][0]["children"][0]["prose"][0]
    assert "`@auth-02`" in prose
    assert "@auth-x は参照" in prose
    assert out["root"][0]["children"][0]["refs"] == ["auth-x"]


def test_rename_rejects_existing_id(doc):
    with pytest.raises(PynValidationError, match="既に存在"):
        patch.rename(doc, "auth-01", "auth-02")


def test_rename_rejects_invalid_id(doc):
    with pytest.raises(PynValidationError, match="不正な id"):
        patch.rename(doc, "auth-01", "auth 01")


# --------------------------------------------------------------------------
# 宣言的パッチ
# --------------------------------------------------------------------------

def test_apply_runs_ops_in_order(doc):
    out = patch.apply(doc, [
        {"op": "set_attr", "id": "auth-01", "key": "status", "value": "done"},
        {"op": "add", "node": {"id": "auth-03", "type": "task"}, "parent": "auth"},
        {"op": "move", "id": "auth-03", "parent": "auth-01"},
    ])
    assert out["root"][0]["children"][0]["attrs"]["status"] == "done"
    assert out["root"][0]["children"][0]["children"][0]["id"] == "auth-03"


def test_apply_is_all_or_nothing(doc):
    before = canonicalize(doc)
    with pytest.raises(PynValidationError, match=r"op\[1\]"):
        patch.apply(doc, [
            {"op": "set_attr", "id": "auth-01", "key": "status", "value": "done"},
            {"op": "set_attr", "id": "nope", "key": "k", "value": 1},
        ])
    assert canonicalize(doc) == before


def test_apply_rejects_unknown_op(doc):
    with pytest.raises(PynValidationError, match="未知の操作"):
        patch.apply(doc, [{"op": "explode", "id": "auth-01"}])


def test_apply_reports_missing_key(doc):
    with pytest.raises(PynValidationError, match="必須のキー"):
        patch.apply(doc, [{"op": "set_attr", "id": "auth-01"}])


def test_apply_empty_ops_is_canonicalize(doc):
    assert patch.apply(doc, []) == canonicalize(doc)


# --------------------------------------------------------------------------
# 行番号ではなく ID で当たること(s-01 の眼目)
# --------------------------------------------------------------------------

def test_patch_lands_regardless_of_projection(doc):
    """同じ op を投影A由来と投影B由来の文書に当てて、結果が一致すること。"""
    ops = [{"op": "set_attr", "id": "auth-02", "key": "status", "value": "done"}]
    from_a = patch.apply(parse(to_pyn_a(doc)), ops)
    from_b = patch.apply(parse(to_pyn_b(doc)), ops)
    assert from_a == from_b


def test_patch_lands_after_reindentation(doc):
    """インデント幅が変わっても ID 宛のパッチは同じ場所に当たる。"""
    reindented = to_pyn_a(doc).replace("  ", "    ")
    ops = [{"op": "set_attr", "id": "auth-02", "key": "status", "value": "done"}]
    assert patch.apply(parse(reindented), ops) == patch.apply(doc, ops)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def test_cli_patch_writes_same_projection(tmp_path):
    src = tmp_path / "doc.pyn"
    src.write_text(SRC, encoding="utf-8")
    ops = tmp_path / "ops.json"
    ops.write_text(json.dumps(
        [{"op": "set_attr", "id": "auth-01", "key": "status", "value": "done"}]
    ), encoding="utf-8")

    proc = subprocess.run(
        [sys.executable, "-m", "pyn", "patch", str(src), "--ops", str(ops)],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
    )
    assert proc.returncode == 0, proc.stderr
    out = parse(src.read_text(encoding="utf-8"))
    assert out["root"][0]["children"][0]["attrs"]["status"] == "done"
    assert "  task auth-01" in src.read_text(encoding="utf-8")  # 投影A のまま


def test_cli_patch_can_switch_projection(tmp_path):
    src = tmp_path / "doc.pyn"
    src.write_text(SRC, encoding="utf-8")
    ops = tmp_path / "ops.json"
    ops.write_text(json.dumps([{"op": "move", "id": "auth-02", "parent": None}]),
                   encoding="utf-8")
    out = tmp_path / "out.pyn"

    proc = subprocess.run(
        [sys.executable, "-m", "pyn", "patch", str(src), "--ops", str(ops),
         "--as", "b", "-o", str(out)],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
    )
    assert proc.returncode == 0, proc.stderr
    text = out.read_text(encoding="utf-8")
    assert "^auth" in text
    assert src.read_text(encoding="utf-8") == SRC  # -o 指定時は原本を触らない


def test_cli_patch_fails_on_dangling_ref(tmp_path):
    src = tmp_path / "doc.pyn"
    src.write_text(SRC, encoding="utf-8")
    ops = tmp_path / "ops.json"
    ops.write_text(json.dumps([{"op": "remove", "id": "auth-01"}]), encoding="utf-8")

    proc = subprocess.run(
        [sys.executable, "-m", "pyn", "patch", str(src), "--ops", str(ops)],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
    )
    assert proc.returncode == 1
    assert src.read_text(encoding="utf-8") == SRC  # 失敗時は書き換えない
