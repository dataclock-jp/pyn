"""s-02: インラインを「装飾」ではなく「グラフの辺」にする。

散文の中の ``@id`` / ``§id`` は表示上のリンクではなく、
文書のID空間への参照 = グラフの辺である。
パーサはこれを実データとして抽出し、レンダラがリンクへ解決する。

したがって ``refs`` は *派生値* であり、.pyn の表層構文には現れない。
往復(parse → format → parse)で自動的に再計算されるため、
round-trip の等価性を壊さない。
"""

from __future__ import annotations

import re

from .errors import PynRefError

#: 散文中の参照記法。@id と §id の2種類のみを認める。
#: コード中のメールアドレス等を拾わないよう、直前が英数字の場合は参照としない。
#: 参照は英数字かアンダースコアで終わる。ID には `.` と `-` も使えるが、末尾に来るものは
#: 参照に含めない。含めると、文末に参照を書いたときのピリオドまで ID になり、
#: リンク切れとして落ちていた(t-31)。
REF_RE = re.compile(
    r"(?<![A-Za-z0-9_])[@§]([A-Za-z0-9_](?:[A-Za-z0-9_.-]*[A-Za-z0-9_])?)")

#: リンクの行き先 `](...)` と、裸の URL。この中の `@` は参照ではない
#: (`https://example.com/@name/post` の `name` が参照になり、正当な URL で検査が落ちていた)。
#: 裸の URL は ASCII の URL 文字だけを続きと見なす。日本語の文では URL の直後に空白を
#: 置かないので、空白までを URL にすると後ろの文の参照まで巻き込む。
LINK_DEST_RE = re.compile(r"\]\([^)\s]*\)")
URL_RE = re.compile(r"(?i)(?<![A-Za-z0-9])(?:https?|mailto):[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+")

#: Markdown のインラインコード。参照解決の対象外にする。
CODE_SPAN_RE = re.compile(r"`[^`\n]*`")

#: フェンス付きコードブロック。同じく対象外。
FENCE_RE = re.compile(r"^\s*(```|~~~)")


def strip_uncheckable(text: str) -> str:
    """参照抽出の対象外領域(コード)を空白で潰した文字列を返す。

    長さを保つため同数の空白に置換する。オフセットを保てるので、
    将来レンダラ側で位置情報を使う余地を残せる。
    """
    lines = text.split("\n")
    out: list[str] = []
    in_fence = False
    for line in lines:
        if FENCE_RE.match(line):
            in_fence = not in_fence
            out.append(" " * len(line))
            continue
        if in_fence:
            out.append(" " * len(line))
            continue
        out.append(mask_urls(CODE_SPAN_RE.sub(lambda m: " " * len(m.group(0)), line)))
    return "\n".join(out)


def mask_urls(text: str) -> str:
    """リンクの行き先と裸の URL を、同じ長さの空白で潰す(位置は変えない)。"""
    blank = lambda m: " " * len(m.group(0))  # noqa: E731
    return URL_RE.sub(blank, LINK_DEST_RE.sub(blank, text))


def sub_refs(text: str, repl) -> str:
    """URL の外にある参照だけを ``repl(match)`` の結果に置き換える。

    レンダラ用。コードスパンとフェンスは呼び出し側が先に退避しておく。抽出
    (:func:`extract_refs`)と同じ規則で置き換えるので、「辺として数えたもの」と
    「リンクとして描いたもの」が食い違わない。``match.group(0)[0]`` が記号、
    ``match.group(1)`` が ID。
    """
    out: list[str] = []
    last = 0
    for m in REF_RE.finditer(mask_urls(text)):
        out.append(text[last:m.start()])
        out.append(repl(m))
        last = m.end()
    out.append(text[last:])
    return "".join(out)


def extract_refs(prose_blocks) -> list[str]:
    """散文ブロック列から参照IDを出現順・重複除去で取り出す。"""
    seen: dict[str, None] = {}
    for block in prose_blocks:
        for m in REF_RE.finditer(strip_uncheckable(block)):
            seen.setdefault(m.group(1), None)
    return list(seen)


def iter_nodes(doc):
    """文書内の全ノードを深さ優先の出現順で列挙する。"""
    stack = list(reversed(doc.get("root", [])))
    while stack:
        node = stack.pop()
        yield node
        for child in reversed(node.get("children", [])):
            stack.append(child)


def index_by_id(doc) -> dict:
    """id -> node の索引。重複IDは呼び出し側(canonicalize)で検出済みの前提。"""
    return {n["id"]: n for n in iter_nodes(doc)}


def validate_refs(doc) -> None:
    """未定義の参照があれば PynRefError を送出する。

    「リンク切れが CI で落ちる」という s-02 の約束の実装本体。
    """
    index = index_by_id(doc)
    dangling: list[tuple[str, str]] = []
    for node in iter_nodes(doc):
        for ref in node.get("refs", []):
            if ref not in index:
                dangling.append((node["id"], ref))
    if dangling:
        detail = ", ".join(f"{src} -> @{dst}" for src, dst in dangling)
        raise PynRefError(
            f"未定義のIDを参照しています ({len(dangling)}件): {detail}"
        )


def edges(doc) -> list[tuple[str, str]]:
    """(参照元ID, 参照先ID) の辺リスト。文書がグラフであることの実体。"""
    return [
        (node["id"], ref)
        for node in iter_nodes(doc)
        for ref in node.get("refs", [])
    ]
