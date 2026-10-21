"""正準形 JSON → `.pyn`(投影A / 投影B)。

s-01 の実装本体。同じ正準形から2つの射影を作り、両者を可逆に往復させる。

  投影A: インデント形。人間が読み書きする。構造がインデントで見える。
  投影B: フラット形。インデントに意味がなく、親子は ^parent だけが決める。
         AI の差分適用・パッチはこちらに当てる(インデント破損が起こり得ない)。

ノード内の出力順は attrs → prose → children に正準化する。
入力での並びは正準形の時点で失われているので、往復は正準形を基準に安定する。
投影A では子ノードの前に空行を置く(人が書く形に合わせ、patch の差分を小さくする)。
"""

from __future__ import annotations

from .model import PROSE_DELIM, canonicalize
from .values import emit_value, quote

INDENT_UNIT = 2


def to_pyn(doc: dict, projection: str = "A") -> str:
    p = projection.upper()
    if p == "A":
        return to_pyn_a(doc)
    if p == "B":
        return to_pyn_b(doc)
    raise ValueError(f"未知の射影: {projection!r}(A または B)")


# --------------------------------------------------------------------------
# 投影A: インデント形
# --------------------------------------------------------------------------

def to_pyn_a(doc: dict) -> str:
    doc = canonicalize(doc)
    out: list[str] = []
    for i, node in enumerate(doc["root"]):
        if i:
            out.append("")
        _emit_a(node, 0, out)
    return "\n".join(out) + "\n" if out else ""


def _emit_a(node: dict, level: int, out: list[str]) -> None:
    ind = " " * (INDENT_UNIT * level)
    child_ind = " " * (INDENT_UNIT * (level + 1))

    out.append(f"{ind}{_decl(node)}:")

    for key, value in node.get("attrs", {}).items():
        out.append(f"{child_ind}{key}: {emit_value(value)}")

    for block in node.get("prose", []):
        out.append(f"{child_ind}{PROSE_DELIM}")
        for line in block.split("\n"):
            out.append(f"{child_ind}{line}" if line else "")
        out.append(f"{child_ind}{PROSE_DELIM}")

    # 子ノードの前には空行を置く(宣言の直後に来る最初の子だけは詰める)。
    # 人が書く .pyn と同じ見た目にしておかないと、patch のたびに空行の削除が
    # 差分に混ざり、本当の変更が埋もれる。
    for i, child in enumerate(node.get("children", [])):
        if i or node.get("attrs") or node.get("prose"):
            out.append("")
        _emit_a(child, level + 1, out)


# --------------------------------------------------------------------------
# 投影B: フラット形
# --------------------------------------------------------------------------

def to_pyn_b(doc: dict) -> str:
    doc = canonicalize(doc)
    out: list[str] = []
    for node in doc["root"]:
        _emit_b(node, None, out)
    return "\n".join(out) + "\n" if out else ""


def _emit_b(node: dict, parent_id: str | None, out: list[str]) -> None:
    decl = _decl(node)
    if parent_id is not None:
        decl += f" ^{parent_id}"
    out.append(decl)

    for key, value in node.get("attrs", {}).items():
        out.append(f"{key}: {emit_value(value)}")

    for block in node.get("prose", []):
        out.append(PROSE_DELIM)
        for line in block.split("\n"):
            out.append(line)
        out.append(PROSE_DELIM)

    # DFS 前順。投影A→B→A で順序が保存される条件。
    for child in node.get("children", []):
        _emit_b(child, node["id"], out)


# --------------------------------------------------------------------------

def _decl(node: dict) -> str:
    decl = f"{node['type']} {node['id']}"
    title = node.get("title")
    if title is not None:
        decl += f" {quote(title)}"
    return decl
