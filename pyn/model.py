"""正準形(canonical JSON)のモデルと正準化。

s-meta:
    真実は「型とIDを持つノードの集合と、その間の辺」である。
    インデント形もフラット形も JSON も HTML も、すべて再生成可能な射影にすぎない。

このモジュールがその「真実」を定義する。他のモジュールは全部射影。

正準化の規則(バイト安定性のため):
  1. ノードのキー順は id, type, title, attrs, prose, refs, children に固定
  2. 空の値(title=None, attrs/prose/refs/children が空)は省略
  3. attrs のキーは昇順ソート
  4. prose は各行を rstrip し、前後の空行を除去
  5. refs は prose から再計算(派生値なので入力値は無視)
"""

from __future__ import annotations

import json
import re

from .errors import PynValidationError
from .refs import extract_refs, iter_nodes

CANONICAL_VERSION = "1.0"

ID_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*$")
TYPE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")

#: ノードのキー順(正準形の定義そのもの)
NODE_KEY_ORDER = ("id", "type", "title", "attrs", "prose", "refs", "children")

PROSE_DELIM = '"""'

_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_HEADING_RE = re.compile(r"^#{1,6}\s")


def new_doc(root=None) -> dict:
    return {"pyn": CANONICAL_VERSION, "root": list(root or [])}


def new_node(node_id: str, node_type: str, title: str | None = None) -> dict:
    node: dict = {"id": node_id, "type": node_type}
    if title is not None:
        node["title"] = title
    return node


# --------------------------------------------------------------------------
# 正準化
# --------------------------------------------------------------------------

def canonicalize(doc: dict) -> dict:
    """任意の文書 dict を正準形へ写す。冪等であること(t-04 で検証)。"""
    if not isinstance(doc, dict):
        raise PynValidationError("document は dict である必要があります")

    root = [_canon_node(n, path="root") for n in doc.get("root", [])]
    out = {"pyn": CANONICAL_VERSION, "root": root}
    _check_duplicate_ids(out)
    return out


def _canon_node(node: dict, path: str) -> dict:
    if not isinstance(node, dict):
        raise PynValidationError(f"{path}: ノードは dict である必要があります")

    node_id = node.get("id")
    node_type = node.get("type")
    if not isinstance(node_id, str) or not ID_RE.match(node_id):
        raise PynValidationError(f"{path}: 不正な id: {node_id!r}")
    if not isinstance(node_type, str) or not TYPE_RE.match(node_type):
        raise PynValidationError(f"{path}: 不正な type: {node_type!r}")

    here = f"{path}/{node_id}"

    out: dict = {"id": node_id, "type": node_type}

    title = node.get("title")
    if title is not None:
        if not isinstance(title, str):
            raise PynValidationError(f"{here}: title は文字列である必要があります")
        if "\n" in title or "\r" in title:
            raise PynValidationError(f"{here}: title に改行は含められません")
        out["title"] = title

    attrs = node.get("attrs") or {}
    if attrs:
        if not isinstance(attrs, dict):
            raise PynValidationError(f"{here}: attrs は dict である必要があります")
        canon_attrs = {}
        for key in sorted(attrs):
            if not TYPE_RE.match(key):
                raise PynValidationError(f"{here}: 不正な属性キー: {key!r}")
            canon_attrs[key] = _canon_value(attrs[key], here, key)
        out["attrs"] = canon_attrs

    prose = node.get("prose") or []
    if prose:
        if isinstance(prose, str):
            prose = [prose]
        blocks = [_canon_prose(p, here) for p in prose]
        blocks = [b for b in blocks if b != ""]
        if blocks:
            out["prose"] = blocks

    refs = extract_refs(out.get("prose", []))
    if refs:
        out["refs"] = refs

    children = node.get("children") or []
    if children:
        out["children"] = [_canon_node(c, here) for c in children]

    return out


def _canon_value(value, here: str, key: str):
    if isinstance(value, list):
        return [_canon_scalar(v, here, key) for v in value]
    return _canon_scalar(value, here, key)


def _canon_scalar(value, here: str, key: str):
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    raise PynValidationError(
        f"{here}: 属性 {key!r} の値がスカラーではありません: {type(value).__name__}"
        " — 構造を持たせたい場合は子ノードで表現してください"
    )


def _canon_prose(block, here: str) -> str:
    if not isinstance(block, str):
        raise PynValidationError(f"{here}: 散文ブロックは文字列である必要があります")
    lines = [line.rstrip() for line in block.replace("\r\n", "\n").split("\n")]
    while lines and lines[0] == "":
        lines.pop(0)
    while lines and lines[-1] == "":
        lines.pop()
    in_fence = False
    for line in lines:
        if line.strip() == PROSE_DELIM:
            raise PynValidationError(
                f'{here}: 散文ブロック内に単独の {PROSE_DELIM} は置けません'
            )
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if not in_fence and _HEADING_RE.match(line):
            # q-03: 見出しはノード構造の専有物。散文に置くと Markdown 射影(t-13)で
            # 階層が二重定義になり、ノードの深さと矛盾する。
            raise PynValidationError(
                f"{here}: 散文中に Markdown 見出し(#)は置けません: {line.strip()!r}"
                " — 見出しにしたい単位は子ノードで表現してください(q-03)"
            )
    return "\n".join(lines)


def _check_duplicate_ids(doc: dict) -> None:
    seen: dict[str, int] = {}
    for node in iter_nodes(doc):
        seen[node["id"]] = seen.get(node["id"], 0) + 1
    dupes = sorted(k for k, v in seen.items() if v > 1)
    if dupes:
        raise PynValidationError(
            "IDが重複しています: " + ", ".join(dupes)
            + " — s-01 は ID の一意性を前提としています(cost-1)"
        )


# --------------------------------------------------------------------------
# 正準 JSON のシリアライズ
# --------------------------------------------------------------------------

def dumps(doc: dict) -> str:
    """正準 JSON テキスト。同じ文書からは常に同じバイト列が出ること。"""
    return json.dumps(canonicalize(doc), ensure_ascii=False, indent=2) + "\n"


def loads(text: str) -> dict:
    return canonicalize(json.loads(text))
