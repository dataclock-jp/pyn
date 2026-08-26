"""正準形 JSON → Markdown(t-13)。

d-00 の原則は HTML だけの話ではない。手書きの `.md` が正本のそばに置かれた瞬間、
それは二重管理である。Markdown も *射影* にする、というのが t-13。

HTML 射影(t-06)との違いは 3 つだけ:

  * 見出しはノードの深さから作る。q-03 で「散文中の見出しを禁止」したのは、
    まさに見出しをノード構造の専有物にしておくため。ここでその決定が効く。
  * 散文の Markdown は解釈せず素通しする。射影先が同じ Markdown なのだから、
    パースして組み直す理由がない(s-02 の「純装飾は素通し」がそのまま働く)。
  * ``@id`` だけは辺なので ``[タイトル](#id)`` へ解決する。リンクテキストは
    参照先のタイトルから補完するので、二重管理が生まれない。

HTML 射影が出す「→ 参照」のチップ列に相当するものは出さない。``refs`` は散文から
導出される派生値なので、散文をそのまま通す Markdown では必ず本文中に同じリンクが
現れており、一覧を足すと同じ辺を二度書くことになる(HTML では一覧が別の役目
—— 折りたたまれた本文を読まずに辺だけ辿る —— を持つので残してある)。

HTML と同じく一方向の射影である(Markdown からは型・ID 空間が復元できない)。
往復が要求されるのは .pyn ⇄ 正準形 の間だけ(s-03)。
"""

from __future__ import annotations

import re

from .model import canonicalize
from .refs import index_by_id
from .values import emit_value

#: 散文中の参照。render_html と同じ形。
REF = re.compile(r"(?<![A-Za-z0-9_])([@§])([A-Za-z0-9_][A-Za-z0-9_.-]*)")

CODE_SPAN = re.compile(r"`[^`\n]*`")
FENCE = re.compile(r"^\s*(```|~~~)")

#: Markdown の見出しレベルの上限。これ以上深いノードは太字で表現する。
MAX_HEADING = 6


def render_md(doc: dict, *, title: str | None = None,
              subtitle: str | None = None,
              note: str | None = None,
              base_level: int = 2) -> str:
    """正準形から Markdown 文書を生成する。

    base_level はルートノードの見出しレベル。既定の 2 は、文書タイトルに
    ``#`` を使う前提。
    """
    doc = canonicalize(doc)
    index = index_by_id(doc)

    out: list[str] = []
    if title:
        out.append(f"# {title}")
        out.append("")
    if subtitle:
        out.append(f"*{subtitle}*")
        out.append("")
    if note:
        out.append(f"> {note}")
        out.append("")

    for node in doc["root"]:
        _emit(node, base_level, index, out)

    text = "\n".join(out)
    return _squeeze_blank_lines(text).rstrip("\n") + "\n"


# --------------------------------------------------------------------------

def _emit(node: dict, level: int, index: dict, out: list[str]) -> None:
    out.append(_heading(node, level))
    out.append("")

    attrs = node.get("attrs") or {}
    if attrs:
        for key, value in attrs.items():
            out.append(f"- `{key}`: {_attr_value(value, index)}")
        out.append("")

    for block in node.get("prose", []):
        out.append(_resolve_refs(block, index))
        out.append("")

    for child in node.get("children", []):
        _emit(child, level + 1, index, out)


def _heading(node: dict, level: int) -> str:
    """見出し行。``@id`` からの参照先になるよう明示アンカーを置く。

    GitHub 等の自動アンカーはタイトル文字列から作られるため、ID 空間と一致しない。
    真実は ID の側にあるので(s-meta)、アンカーは ID で固定する。
    """
    anchor = f'<a id="{node["id"]}"></a>'
    label = node.get("title") or node["id"]
    tag = f'`{node["type"]}` `{node["id"]}`'

    if level <= MAX_HEADING:
        return f"{'#' * level} {anchor}{label}\n\n<sub>{tag}</sub>"
    # 見出しの深さを使い切ったら、構造を太字に落とす(Markdown 側の制約)
    return f"{anchor}**{label}** <sub>{tag}</sub>"


def _attr_value(value, index: dict) -> str:
    if isinstance(value, list):
        return ", ".join(f"`{emit_value(v)}`" for v in value) or "`[]`"
    return f"`{emit_value(value)}`"


def _resolve_refs(block: str, index: dict) -> str:
    """散文はそのまま通し、参照だけをリンクへ置き換える。

    コードスパンとフェンス内は s-02 により参照ではないので触らない。
    """
    lines = block.split("\n")
    out: list[str] = []
    in_fence = False
    for line in lines:
        if FENCE.match(line):
            in_fence = not in_fence
            out.append(line)
            continue
        out.append(line if in_fence else _resolve_line(line, index))
    return "\n".join(out)


def _resolve_line(line: str, index: dict) -> str:
    # コードスパンを退避してから参照を解決する
    stash: list[str] = []

    def _keep(m):
        stash.append(m.group(0))
        return f"\x00{len(stash) - 1}\x00"

    masked = CODE_SPAN.sub(_keep, line)

    def _sub(m):
        node = index.get(m.group(2))
        if node is None:
            return m.group(0)  # 未定義参照は書き換えない(検出は validate_refs の仕事)
        return f'[{node.get("title") or m.group(2)}](#{m.group(2)})'

    masked = REF.sub(_sub, masked)
    for i, chunk in enumerate(stash):
        masked = masked.replace(f"\x00{i}\x00", chunk)
    return masked


def _squeeze_blank_lines(text: str) -> str:
    out: list[str] = []
    blank = False
    for line in text.split("\n"):
        if line.strip() == "":
            if blank:
                continue
            blank = True
        else:
            blank = False
        out.append(line)
    return "\n".join(out)
