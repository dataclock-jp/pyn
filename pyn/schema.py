"""t-10: JSON Schema と ``canonicalize`` の二重定義を解消する。

これまで正準形の定義は 2 箇所にあった:

  * ``pyn/model.py`` の ``canonicalize``(実際に使われる検証)
  * ``schema/canonical.schema.json``(手書きの JSON Schema)

同じ規則を 2 回書いている以上、いつか食い違う。d-00 が HTML について言っていることは
schema にもそのまま当てはまる —— **どちらかを生成物にする**。

ここでは ``model.py`` を単一の源とし、schema は ``build_schema()`` の出力
(=生成物)とした。ID/型の正規表現も、正準形のバージョンも、ノードのキー順も、
すべて ``model`` の定数から作られる。schema ファイルはリポジトリに置いたままだが、
それは「生成物をコミットしてある」という意味であり、tests/test_schema.py が
追従を強制する(Handover.html と同じ扱い)。

もうひとつ必要なのが「突き合わせ」である。生成しただけでは、schema の表現と
``canonicalize`` の実装が同じものを認めている保証にならない。そこで外部依存なしで
schema を解釈する検証器(``validate``)を置き、``canonicalize`` の出力が常に
schema に適合することをテストで確かめる。実装している語彙は本 schema が使うものだけ:

    $ref / $defs / type / const / pattern / enum / required / properties /
    additionalProperties / propertyNames / items / minItems / minProperties / anyOf

``canonicalize`` は schema より **狭い**(重複IDの禁止、散文中の見出し禁止など、
JSON Schema では書けない規則を持つ)。したがって突き合わせは片側の包含関係
「canonicalize の出力 ⊆ schema 適合」として検証する。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .errors import PynValidationError
from .model import CANONICAL_VERSION, ID_RE, NODE_KEY_ORDER, TYPE_RE

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schema" / "canonical.schema.json"

SCHEMA_ID = "https://example.invalid/pyn/canonical.schema.json"


class PynSchemaError(PynValidationError):
    """正準形が JSON Schema に適合しない。"""


# --------------------------------------------------------------------------
# schema の生成(単一の源は model.py)
# --------------------------------------------------------------------------

def build_schema() -> dict:
    """正準形の JSON Schema を組み立てる。手書きしないこと。"""
    node_props = {
        "id": {"$ref": "#/$defs/identifier"},
        "type": {"$ref": "#/$defs/typeName"},
        "title": {
            "type": "string",
            "description": "表示名。改行を含めない。省略可(空なら省略する)。",
            "pattern": "^[^\\n\\r]*$",
        },
        "attrs": {
            "type": "object",
            "description": "スカラー属性(HTMLのattribute / JSONのkey-value)。"
                           "キーは昇順にソートして正準化する。",
            "minProperties": 1,
            "propertyNames": {"$ref": "#/$defs/typeName"},
            "additionalProperties": {"$ref": "#/$defs/value"},
        },
        "prose": {
            "type": "array",
            "description": '散文ブロック(.pyn では """ ... """)。中身は Markdown 素通し。'
                           "各行は rstrip 済み、前後の空行は除去済み。"
                           "Markdown 見出し(#)は含められない(q-03)。",
            "minItems": 1,
            "items": {"type": "string"},
        },
        "refs": {
            "type": "array",
            "description": "s-02。prose 内の @id / §id から機械的に抽出した参照"
                           "(=グラフの辺)。派生値であり .pyn の表層には現れない。"
                           "出現順・重複除去済み。",
            "minItems": 1,
            "items": {"$ref": "#/$defs/identifier"},
        },
        "children": {
            "type": "array",
            "description": "子ノード(HTMLのchildren)。順序が意味を持つ。",
            "minItems": 1,
            "items": {"$ref": "#/$defs/node"},
        },
    }
    # キー順は正準形の定義そのもの(model.NODE_KEY_ORDER)。schema 側でも同じ順に並べる。
    ordered_props = {key: node_props[key] for key in NODE_KEY_ORDER}

    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": SCHEMA_ID,
        "title": "PYN canonical document",
        "description": (
            "PYN の正準形(canonical JSON)。これが正本であり、.pyn の投影A/投影B と"
            " HTML / Markdown はすべてここからの射影。キー順は "
            + ", ".join(NODE_KEY_ORDER)
            + " に固定し、空の値は省略することでバイト安定性を担保する。"
            " このファイルは pyn.schema.build_schema() の生成物であり、手で編集しない"
            "(t-10)。生成: python -m tools.build_schema"
        ),
        "type": "object",
        "required": ["pyn", "root"],
        "additionalProperties": False,
        "properties": {
            "pyn": {
                "description": "正準形のバージョン",
                "const": CANONICAL_VERSION,
            },
            "root": {
                "description": "ルート直下のノード列(順序が意味を持つ)",
                "type": "array",
                "items": {"$ref": "#/$defs/node"},
            },
        },
        "$defs": {
            "identifier": {
                "type": "string",
                "pattern": ID_RE.pattern,
                "description": "文書内で一意。s-01(構造をインデントからIDへ移す)の"
                               "前提となるため必須。",
            },
            "typeName": {
                "type": "string",
                "pattern": TYPE_RE.pattern,
                "description": "ノードの型。HTMLのclass名と違い第一級の意味を持つ"
                               "(task / decision / section など)。",
            },
            "scalar": {
                "description": "属性のスカラー値。YAMLの暗黙型変換は採用しない"
                               "(no/yes/on/off は文字列のまま)。",
                "type": ["string", "number", "boolean", "null"],
            },
            "value": {
                "description": "属性値。スカラー、またはスカラーの配列。"
                               "ネストした構造は子ノードで表現する。",
                "anyOf": [
                    {"$ref": "#/$defs/scalar"},
                    {"type": "array", "items": {"$ref": "#/$defs/scalar"}},
                ],
            },
            "node": {
                "type": "object",
                "required": ["id", "type"],
                "additionalProperties": False,
                "properties": ordered_props,
            },
        },
    }


def dumps_schema() -> str:
    return json.dumps(build_schema(), ensure_ascii=False, indent=2) + "\n"


def load_schema() -> dict:
    """リポジトリに置かれた schema ファイルを読む(生成物の実体)。"""
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# 検証器(この schema が使う語彙だけの部分実装)
# --------------------------------------------------------------------------

_TYPES = {
    "object": dict,
    "array": list,
    "string": str,
    "boolean": bool,
    "null": type(None),
}


def validate(doc, schema: dict | None = None) -> None:
    """JSON Schema に適合しなければ PynSchemaError を送出する。

    外部依存を入れないための最小実装。未知のキーワードは無視せず、
    「この検証器が知らない語彙を schema が使い始めた」ことを不適合として扱う
    —— 黙って検証を素通しさせるほうが危険だから。
    """
    schema = build_schema() if schema is None else schema
    _validate(doc, schema, schema, "$")


_KNOWN = {
    "$schema", "$id", "$defs", "$ref", "title", "description",
    "type", "const", "enum", "pattern",
    "required", "properties", "additionalProperties", "propertyNames",
    "items", "minItems", "minProperties",
    "anyOf",
}


def _fail(path: str, message: str):
    raise PynSchemaError(f"{path}: {message}")


def _resolve(ref: str, root: dict, path: str) -> dict:
    if not ref.startswith("#/"):
        _fail(path, f"外部参照は解決できません: {ref}")
    node = root
    for part in ref[2:].split("/"):
        if not isinstance(node, dict) or part not in node:
            _fail(path, f"解決できない $ref: {ref}")
        node = node[part]
    return node


def _validate(value, schema: dict, root: dict, path: str) -> None:
    unknown = set(schema) - _KNOWN
    if unknown:
        _fail(path, f"検証器が知らないキーワード: {sorted(unknown)}")

    if "$ref" in schema:
        _validate(value, _resolve(schema["$ref"], root, path), root, path)
        return

    if "anyOf" in schema:
        for sub in schema["anyOf"]:
            try:
                _validate(value, sub, root, path)
                break
            except PynSchemaError:
                continue
        else:
            _fail(path, "anyOf のどれにも適合しません")

    if "type" in schema:
        _check_type(value, schema["type"], path)

    if "const" in schema and value != schema["const"]:
        _fail(path, f"{schema['const']!r} である必要があります: {value!r}")

    if "enum" in schema and value not in schema["enum"]:
        _fail(path, f"{schema['enum']!r} のいずれかである必要があります: {value!r}")

    if "pattern" in schema:
        if not isinstance(value, str) or not re.search(schema["pattern"], value):
            _fail(path, f"パターン {schema['pattern']!r} に一致しません: {value!r}")

    if isinstance(value, dict):
        _validate_object(value, schema, root, path)
    elif isinstance(value, list):
        _validate_array(value, schema, root, path)


def _check_type(value, expected, path: str) -> None:
    names = [expected] if isinstance(expected, str) else list(expected)
    for name in names:
        if name == "number":
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return
            continue
        if name == "boolean":
            if isinstance(value, bool):
                return
            continue
        py = _TYPES.get(name)
        if py is None:
            _fail(path, f"検証器が知らない type: {name}")
        if isinstance(value, py):
            return
    _fail(path, f"型が {names} ではありません: {type(value).__name__}")


def _validate_object(value: dict, schema: dict, root: dict, path: str) -> None:
    for key in schema.get("required", []):
        if key not in value:
            _fail(path, f"必須プロパティがありません: {key}")

    if "minProperties" in schema and len(value) < schema["minProperties"]:
        _fail(path, f"プロパティが {schema['minProperties']} 個未満です"
                    " — 空の値は省略ではなく削除してください")

    props = schema.get("properties", {})
    extra = schema.get("additionalProperties", True)
    names = schema.get("propertyNames")

    for key, item in value.items():
        here = f"{path}.{key}"
        if names is not None:
            _validate(key, names, root, f"{path}(キー {key!r})")
        if key in props:
            _validate(item, props[key], root, here)
        elif extra is False:
            _fail(here, "未知のプロパティです")
        elif isinstance(extra, dict):
            _validate(item, extra, root, here)


def _validate_array(value: list, schema: dict, root: dict, path: str) -> None:
    if "minItems" in schema and len(value) < schema["minItems"]:
        _fail(path, f"要素が {schema['minItems']} 個未満です"
                    " — 空配列は省略ではなく削除してください")
    items = schema.get("items")
    if items is not None:
        for i, item in enumerate(value):
            _validate(item, items, root, f"{path}[{i}]")


#: パッケージ名前空間では validate_refs と対になる名前で公開する。
validate_schema = validate
