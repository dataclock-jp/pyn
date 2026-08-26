"""t-11: 投影B へのパッチ適用 API。

s-01 の実運用部分。投影B(フラット形)は「インデントに意味がない」形なので、
AI が差分を当てる先として安全である。しかし *どこに* 当てるかは行番号ではなく
ID で指定されるべきで、そうであれば操作はテキストではなく正準形に対して定義できる。

    テキストにパッチを当てる  →  行番号がずれると壊れる
    正準形にパッチを当てる    →  ID が生きている限り壊れない

したがって本モジュールは「投影B のテキストを編集する関数」ではなく、
「ID で宛先を指す操作を正準形に適用し、射影を再生成させる関数」を提供する。
投影B はその結果を人間と AI が読むための表示にすぎない(s-meta)。

すべての操作は純粋関数である。入力の doc は変更せず、正準化済みの新しい doc を返す。

    doc = apply(doc, [
        {"op": "set_attr", "id": "t-11", "key": "status", "value": "done"},
        {"op": "move", "id": "t-11", "parent": "tasks"},
    ])

宣言的な op リストを受ける ``apply`` があるのは、パッチそれ自体もデータであって
テキストではない、という s-meta の帰結を運用に通すため。AI は JSON を吐けばよく、
インデントも引用符も生成しなくてよい。
"""

from __future__ import annotations

import copy

from .errors import PynValidationError
from .model import ID_RE, canonicalize
from .refs import REF_RE, index_by_id, iter_nodes, strip_uncheckable

#: apply() が受け付ける操作名 → 関数名
OPS = (
    "set_attr",
    "del_attr",
    "set_title",
    "set_prose",
    "add_prose",
    "add",
    "remove",
    "move",
    "rename",
)


# --------------------------------------------------------------------------
# 内部ユーティリティ
# --------------------------------------------------------------------------

def _prepared(doc: dict) -> dict:
    """正準化したうえで、呼び出し側の doc を壊さないための複製を返す。"""
    return copy.deepcopy(canonicalize(doc))


def _find(doc: dict, node_id: str) -> dict:
    node = index_by_id(doc).get(node_id)
    if node is None:
        raise PynValidationError(
            f"ID が見つかりません: {node_id!r}"
            " — パッチの宛先は既存の ID である必要があります(cost-1)"
        )
    return node


def _siblings_of(doc: dict, node_id: str) -> tuple[list, int]:
    """node_id を含むリスト(root または親の children)と、その中の位置を返す。"""
    lists = [doc["root"]] + [
        n["children"] for n in iter_nodes(doc) if n.get("children")
    ]
    for lst in lists:
        for i, n in enumerate(lst):
            if n["id"] == node_id:
                return lst, i
    raise PynValidationError(f"ID が見つかりません: {node_id!r}")


def _descendant_ids(node: dict) -> set[str]:
    ids = {node["id"]}
    for child in node.get("children", []):
        ids |= _descendant_ids(child)
    return ids


def _insert(target: list, node: dict, index: int | None) -> None:
    if index is None:
        target.append(node)
    else:
        if not isinstance(index, int):
            raise PynValidationError("index は整数である必要があります")
        target.insert(index, node)


# --------------------------------------------------------------------------
# 属性
# --------------------------------------------------------------------------

def set_attr(doc: dict, node_id: str, key: str, value) -> dict:
    """属性を追加または上書きする。値はスカラーまたはスカラーの配列。"""
    doc = _prepared(doc)
    node = _find(doc, node_id)
    attrs = node.setdefault("attrs", {})
    attrs[key] = value
    return canonicalize(doc)


def del_attr(doc: dict, node_id: str, key: str, *, missing_ok: bool = False) -> dict:
    """属性を削除する。"""
    doc = _prepared(doc)
    node = _find(doc, node_id)
    attrs = node.get("attrs") or {}
    if key not in attrs:
        if missing_ok:
            return canonicalize(doc)
        raise PynValidationError(f"{node_id}: 属性 {key!r} がありません")
    del attrs[key]
    return canonicalize(doc)


def set_title(doc: dict, node_id: str, title: str | None) -> dict:
    """タイトルを差し替える。None で削除。"""
    doc = _prepared(doc)
    node = _find(doc, node_id)
    if title is None:
        node.pop("title", None)
    else:
        node["title"] = title
    return canonicalize(doc)


# --------------------------------------------------------------------------
# 散文(refs は派生値なので触らない — canonicalize が再計算する)
# --------------------------------------------------------------------------

def set_prose(doc: dict, node_id: str, blocks) -> dict:
    """散文ブロック列を置き換える。文字列 1 本を渡してもよい。"""
    doc = _prepared(doc)
    node = _find(doc, node_id)
    if isinstance(blocks, str):
        blocks = [blocks]
    node["prose"] = list(blocks)
    node.pop("refs", None)  # 派生値。canonicalize が prose から再計算する
    return canonicalize(doc)


def add_prose(doc: dict, node_id: str, block: str, index: int | None = None) -> dict:
    """散文ブロックを 1 本足す。"""
    doc = _prepared(doc)
    node = _find(doc, node_id)
    blocks = node.setdefault("prose", [])
    _insert(blocks, block, index)
    node.pop("refs", None)
    return canonicalize(doc)


# --------------------------------------------------------------------------
# ノードの追加・削除・移動
# --------------------------------------------------------------------------

def add(doc: dict, node: dict, parent: str | None = None,
        index: int | None = None) -> dict:
    """ノードを追加する。parent=None なら root 直下。

    投影B の ``^parent`` に対応する操作。ID の重複は canonicalize が弾く。
    """
    doc = _prepared(doc)
    new_node = copy.deepcopy(node)
    if not isinstance(new_node, dict) or "id" not in new_node:
        raise PynValidationError("追加するノードは id を持つ dict である必要があります")

    existing = index_by_id(doc)
    for nid in _descendant_ids(new_node):
        if nid in existing:
            raise PynValidationError(
                f"ID が既に存在します: {nid!r}"
                " — 追加ではなく既存ノードへの操作を意図していませんか"
            )

    target = doc["root"] if parent is None else _find(doc, parent).setdefault("children", [])
    _insert(target, new_node, index)
    return canonicalize(doc)


def remove(doc: dict, node_id: str, *, with_children: bool = True) -> dict:
    """ノードを削除する。

    with_children=False のときは子を削除位置に繰り上げる。
    参照(@id)は消さない。リンク切れは validate_refs が検出する仕事であり、
    黙って本文を書き換えるのは cost-3(誤修復)と同じ罪だから。
    """
    doc = _prepared(doc)
    siblings, i = _siblings_of(doc, node_id)
    node = siblings.pop(i)
    if not with_children:
        for offset, child in enumerate(node.get("children", [])):
            siblings.insert(i + offset, child)
    return canonicalize(doc)


def move(doc: dict, node_id: str, parent: str | None,
         index: int | None = None) -> dict:
    """ノードを別の親の下へ移す(部分木ごと)。parent=None で root 直下へ。"""
    doc = _prepared(doc)
    node = _find(doc, node_id)

    if parent is not None:
        if parent in _descendant_ids(node):
            raise PynValidationError(
                f"{node_id!r} を自身の子孫 {parent!r} の下へは移動できません(循環)"
            )

    siblings, i = _siblings_of(doc, node_id)
    siblings.pop(i)

    if parent is None:
        target = doc["root"]
    else:
        target = _find(doc, parent).setdefault("children", [])
    _insert(target, node, index)
    return canonicalize(doc)


# --------------------------------------------------------------------------
# ID の変更(辺の張り替えを伴う)
# --------------------------------------------------------------------------

def rename(doc: dict, old_id: str, new_id: str) -> dict:
    """ID を変更し、文書中の ``@old`` / ``§old`` 参照も張り替える。

    ID はグラフの頂点そのものなので、rename は「表記の変更」ではなく
    「辺の張り替え」である。散文中の参照を同時に更新しないと文書が壊れる。
    コードスパン・フェンス内は参照ではないので(s-02)書き換えない。
    """
    doc = _prepared(doc)
    if not ID_RE.match(new_id):
        raise PynValidationError(f"不正な id: {new_id!r}")
    node = _find(doc, old_id)
    if new_id != old_id and new_id in index_by_id(doc):
        raise PynValidationError(f"ID が既に存在します: {new_id!r}")

    node["id"] = new_id
    for n in iter_nodes(doc):
        prose = n.get("prose")
        if prose:
            n["prose"] = [_retarget(block, old_id, new_id) for block in prose]
            n.pop("refs", None)
    return canonicalize(doc)


def _retarget(block: str, old_id: str, new_id: str) -> str:
    """散文中の参照だけを張り替える。コード領域は strip_uncheckable で除外済み。"""
    masked = strip_uncheckable(block)
    out: list[str] = []
    last = 0
    for m in REF_RE.finditer(masked):
        if m.group(1) != old_id:
            continue
        out.append(block[last:m.start()])
        out.append(block[m.start()] + new_id)  # 記号(@ or §)を保つ
        last = m.end()
    out.append(block[last:])
    return "".join(out)


# --------------------------------------------------------------------------
# 宣言的パッチ
# --------------------------------------------------------------------------

_DISPATCH = {
    "set_attr": lambda doc, op: set_attr(doc, op["id"], op["key"], op["value"]),
    "del_attr": lambda doc, op: del_attr(doc, op["id"], op["key"],
                                         missing_ok=op.get("missing_ok", False)),
    "set_title": lambda doc, op: set_title(doc, op["id"], op.get("title")),
    "set_prose": lambda doc, op: set_prose(doc, op["id"], op["prose"]),
    "add_prose": lambda doc, op: add_prose(doc, op["id"], op["prose"],
                                           op.get("index")),
    "add": lambda doc, op: add(doc, op["node"], op.get("parent"), op.get("index")),
    "remove": lambda doc, op: remove(doc, op["id"],
                                     with_children=op.get("with_children", True)),
    "move": lambda doc, op: move(doc, op["id"], op.get("parent"), op.get("index")),
    "rename": lambda doc, op: rename(doc, op["id"], op["to"]),
}


def apply(doc: dict, ops) -> dict:
    """操作の列を順に適用する。1 つでも失敗したら文書は変更されない(全か無か)。

    途中まで適用された文書は正準形として妥当でも意味的には中途半端なので、
    部分適用を返さない。失敗時は例外に何番目の操作かを含める。
    """
    result = canonicalize(doc)
    for i, op in enumerate(ops):
        if not isinstance(op, dict) or "op" not in op:
            raise PynValidationError(f"op[{i}]: 'op' キーを持つ dict が必要です")
        name = op["op"]
        fn = _DISPATCH.get(name)
        if fn is None:
            raise PynValidationError(
                f"op[{i}]: 未知の操作 {name!r} — 使えるのは {', '.join(OPS)}"
            )
        try:
            result = fn(result, op)
        except KeyError as exc:
            raise PynValidationError(
                f"op[{i}] ({name}): 必須のキーがありません: {exc.args[0]!r}"
            ) from exc
        except PynValidationError as exc:
            raise PynValidationError(f"op[{i}] ({name}): {exc}") from exc
    return result
