"""t-10: JSON Schema と canonicalize の二重定義を解消したことのテスト。

検証したいのは 3 つ:

  1. schema ファイルが生成物として追従している(手書きに戻っていない)
  2. schema の定数が model.py 由来である(パターンを 2 箇所に書いていない)
  3. canonicalize の出力は常に schema に適合する(実装と定義が食い違っていない)

3 は片側の包含関係でしか書けない。canonicalize は schema より狭いからである
(重複ID・散文中の見出し・三連引用符の行など、JSON Schema では表現できない規則を持つ)。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pyn import canonicalize, parse
from pyn.model import CANONICAL_VERSION, ID_RE, NODE_KEY_ORDER, TYPE_RE
from pyn.schema import (
    PynSchemaError,
    SCHEMA_PATH,
    build_schema,
    dumps_schema,
    load_schema,
    validate,
)
from tests.test_roundtrip import docs

ROOT = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------
# 1. 生成物としての追従
# --------------------------------------------------------------------------

def test_schema_file_is_up_to_date():
    assert SCHEMA_PATH.read_text(encoding="utf-8") == dumps_schema(), (
        "schema/canonical.schema.json が古い —"
        " python -m tools.build_schema で再生成してください(t-10)"
    )


def test_build_schema_tool_check_mode():
    from tools.build_schema import main

    assert main(["--check"]) == 0


# --------------------------------------------------------------------------
# 2. 定数の単一化 — 同じ規則を 2 箇所に書いていないこと
# --------------------------------------------------------------------------

def test_patterns_come_from_model():
    schema = load_schema()
    assert schema["$defs"]["identifier"]["pattern"] == ID_RE.pattern
    assert schema["$defs"]["typeName"]["pattern"] == TYPE_RE.pattern


def test_version_comes_from_model():
    assert load_schema()["properties"]["pyn"]["const"] == CANONICAL_VERSION


def test_node_key_order_comes_from_model():
    props = load_schema()["$defs"]["node"]["properties"]
    assert tuple(props) == NODE_KEY_ORDER


# --------------------------------------------------------------------------
# 3. 実装と定義の突き合わせ
# --------------------------------------------------------------------------

@pytest.mark.parametrize("seed,doc", list(docs()))
def test_canonicalize_output_always_validates(seed, doc):
    """乱数生成した文書の正準形が、例外なく schema に適合すること。"""
    validate(doc, load_schema())


def test_sample_and_handover_validate():
    schema = load_schema()
    for source in ("samples/auth-spec.pyn", "Handover.pyn",
                   "docs/prose-dialect.pyn", "docs/agents.pyn"):
        validate(parse((ROOT / source).read_text(encoding="utf-8")), schema)


def test_empty_document_validates():
    validate(canonicalize({"pyn": CANONICAL_VERSION, "root": []}), load_schema())


# --------------------------------------------------------------------------
# 検証器そのものが機能していること(素通ししていないこと)
# --------------------------------------------------------------------------

def _doc(node):
    return {"pyn": CANONICAL_VERSION, "root": [node]}


@pytest.mark.parametrize("bad,reason", [
    ({"pyn": "9.9", "root": []}, "バージョン違い"),
    ({"root": []}, "pyn がない"),
    ({"pyn": CANONICAL_VERSION}, "root がない"),
    ({"pyn": CANONICAL_VERSION, "root": [], "extra": 1}, "未知のトッププロパティ"),
    (_doc({"type": "task"}), "id がない"),
    (_doc({"id": "a", "type": "task", "nope": 1}), "未知のプロパティ"),
    (_doc({"id": "a b", "type": "task"}), "不正な id"),
    (_doc({"id": "a", "type": "1task"}), "不正な type"),
    (_doc({"id": "a", "type": "task", "title": "改行\nあり"}), "title に改行"),
    (_doc({"id": "a", "type": "task", "attrs": {}}), "空の attrs は省略のはず"),
    (_doc({"id": "a", "type": "task", "prose": []}), "空の prose は省略のはず"),
    (_doc({"id": "a", "type": "task", "children": []}), "空の children は省略のはず"),
    (_doc({"id": "a", "type": "task", "attrs": {"k": {"nested": 1}}}), "入れ子の属性値"),
    (_doc({"id": "a", "type": "task", "attrs": {"k": [[1]]}}), "入れ子の配列"),
    (_doc({"id": "a", "type": "task", "prose": [1]}), "散文が文字列でない"),
    (_doc({"id": "a", "type": "task", "refs": ["a b"]}), "不正な参照ID"),
    (_doc({"id": "a", "type": "task", "children": [{"id": "b"}]}), "子に type がない"),
])
def test_validator_rejects(bad, reason):
    with pytest.raises(PynSchemaError):
        validate(bad, load_schema())


def test_validator_rejects_unknown_keyword():
    """知らない語彙を黙って素通ししないこと(見逃しは検証がないより危険)。"""
    with pytest.raises(PynSchemaError, match="知らないキーワード"):
        validate({"pyn": CANONICAL_VERSION, "root": []},
                 {"type": "object", "uniqueItems": True})


def test_validator_accepts_scalar_kinds():
    doc = canonicalize(_doc({
        "id": "a", "type": "task",
        "attrs": {"s": "x", "i": 1, "f": 1.5, "b": True, "n": None, "l": [1, "x"]},
    }))
    validate(doc, load_schema())


def test_boolean_is_not_a_number_for_schema_purposes():
    """JSON Schema 上 true は number ではない。検証器がそこを取り違えないこと。"""
    from pyn.schema import _check_type

    with pytest.raises(PynSchemaError):
        _check_type(True, "number", "$")
    _check_type(True, "boolean", "$")
    _check_type(1, "number", "$")


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def test_cli_check_reports_schema(capsys):
    from pyn.cli import main

    assert main(["check", str(ROOT / "samples" / "auth-spec.pyn")]) == 0
    assert "schema適合" in capsys.readouterr().out
