"""`.pyn` → 正準形 JSON。

投影A(インデント形 / 人間が読み書きする)と
投影B(フラット形 + ^parent / AI がパッチを当てる)の両方を受理する。

s-01 の要点:
    インデントは構造の *定義* ではなく *投影* である。
    投影B にはインデントの意味が一切なく、親子関係は ^parent だけが決める。
    したがって投影B は「インデント破損」という故障モードを構造的に持たない。
"""

from __future__ import annotations

import re

from .errors import PynIndentError, PynSyntaxError
from .model import PROSE_DELIM, canonicalize
from .values import _unescape, parse_value

# 投影A のノード宣言: `task auth-01 "認可エンドポイント":`
NODE_A_RE = re.compile(
    r'^(?P<type>[A-Za-z_][A-Za-z0-9_-]*)'
    r'[ ]+(?P<id>[A-Za-z0-9_][A-Za-z0-9_.-]*)'
    r'(?:[ ]+"(?P<title>(?:[^"\\]|\\.)*)")?'
    r'[ ]*:[ ]*$'
)

# 投影B のノード宣言: `task auth-01 "認可エンドポイント" ^auth`
NODE_B_RE = re.compile(
    r'^(?P<type>[A-Za-z_][A-Za-z0-9_-]*)'
    r'[ ]+(?P<id>[A-Za-z0-9_][A-Za-z0-9_.-]*)'
    r'(?:[ ]+"(?P<title>(?:[^"\\]|\\.)*)")?'
    r'(?:[ ]+\^(?P<parent>[A-Za-z0-9_][A-Za-z0-9_.-]*))?'
    r'[ ]*$'
)

ATTR_RE = re.compile(
    r'^(?P<key>[A-Za-z_][A-Za-z0-9_-]*)[ ]*:[ ]*(?P<val>\S.*?)[ ]*$'
)


def parse(text: str, *, canonical: bool = True) -> dict:
    """.pyn テキストを正準形 dict にする。投影A/B は自動判別。"""
    text = text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")

    mode = _detect_mode(lines)
    if mode is None:
        doc = {"pyn": "1.0", "root": []}
    elif mode == "A":
        doc = _parse_indented(lines)
    else:
        doc = _parse_flat(lines)

    return canonicalize(doc) if canonical else doc


def comment_lines(text: str) -> list[tuple[int, str]]:
    """コメント行を (行番号, 行) で返す(t-14)。

    コメントは正準形に保存されない。``#`` は表層構文であって文書の意味ではなく、
    どのノードに属するかも決まっていないからである(s-meta: 真実はノードグラフ、
    表記は射影)。したがって射影を再生成した瞬間に消える。

    黙って消すのはよくないので、消える前に「何行消えるか」を言えるようにする。
    散文ブロックの中の ``#`` はコメントではない(方言としても見出しは禁止だが、
    フェンス内のコメントや ``#hashtag`` は正当な本文である)ため、
    パーサと同じ規則で散文を読み飛ばして数える。
    """
    text = text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    found: list[tuple[int, str]] = []
    prose_indent: int | None = None

    for i, raw in enumerate(text.split("\n")):
        s = raw.strip()
        if prose_indent is not None:
            if s == PROSE_DELIM and _leading_spaces(raw) == prose_indent:
                prose_indent = None
            continue
        if s == PROSE_DELIM:
            prose_indent = _leading_spaces(raw)
            continue
        if s.startswith("#"):
            found.append((i + 1, raw))
    return found


def detect_projection(text: str) -> str | None:
    """'A' / 'B' / None(空文書)を返す。"""
    text = text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    return _detect_mode(text.split("\n"))


# --------------------------------------------------------------------------

def _is_skippable(raw: str) -> bool:
    s = raw.strip()
    return s == "" or s.startswith("#")


def _detect_mode(lines) -> str | None:
    for raw in lines:
        if _is_skippable(raw):
            continue
        s = raw.strip()
        if s.startswith(PROSE_DELIM):
            # 散文が先頭に来ることはない(必ずノードに属する)
            raise PynSyntaxError(
                "散文ブロックがどのノードにも属していません", None, raw,
                hint="先にノードを宣言してください",
            )
        if NODE_A_RE.match(s):
            return "A"
        if NODE_B_RE.match(s):
            return "B"
        raise PynSyntaxError(
            "文書の先頭はノード宣言である必要があります", None, raw,
            hint='投影A なら `type id "title":`、投影B なら `type id "title" ^parent`',
        )
    return None


def _indent_of(raw: str, line_no: int) -> int:
    n = 0
    for ch in raw:
        if ch == " ":
            n += 1
        elif ch == "\t":
            raise PynIndentError(
                "タブによるインデントは使えません", line_no, raw,
                hint="半角スペースに統一してください(混在は検出できない破損を生みます)",
            )
        else:
            break
    return n


def _title_of(m) -> str | None:
    """タイトルのアンエスケープは values._unescape を共用する。

    素朴な replace の連鎖は `\\\\` と `\\"` が隣接した場合に壊れるため、
    emit 側(values.quote)と対になる走査型のアンエスケープを使う。
    """
    raw = m.group("title")
    if raw is None:
        return None
    return _unescape(raw, None, raw)


# --------------------------------------------------------------------------
# 投影A: インデント形
# --------------------------------------------------------------------------

def _parse_indented(lines) -> dict:
    root: list = []
    # (level, node) のスタック。level=-1 は仮想ルート。
    stack: list[tuple[int, dict | None]] = [(-1, None)]
    unit: int | None = None

    i = 0
    n = len(lines)
    while i < n:
        raw = lines[i]
        line_no = i + 1
        if _is_skippable(raw):
            i += 1
            continue

        indent = _indent_of(raw, line_no)
        s = raw.strip()

        if unit is None and indent > 0:
            unit = indent
        if unit is not None and indent % unit != 0:
            raise PynIndentError(
                f"インデント幅が一定ではありません(単位 {unit} の倍数である必要があります。実際 {indent})",
                line_no, raw,
                hint="s-01: ID階層との冗長性で検出しています。`pyn repair` で修復候補を確認できます",
            )
        level = 0 if unit is None else indent // unit

        current_level = stack[-1][0]
        if level > current_level + 1:
            raise PynIndentError(
                f"インデントが飛んでいます(現在の深さ {current_level} に対して {level})",
                line_no, raw,
            )

        # 散文ブロック
        if s.startswith(PROSE_DELIM):
            if s != PROSE_DELIM:
                raise PynSyntaxError(
                    f"{PROSE_DELIM} は単独の行に置いてください", line_no, raw)
            while stack[-1][0] >= level:
                stack.pop()
            owner = stack[-1][1]
            if owner is None:
                raise PynSyntaxError(
                    "散文ブロックがどのノードにも属していません", line_no, raw)
            block, i = _read_prose(lines, i, indent)
            owner.setdefault("prose", []).append(block)
            continue

        m = NODE_A_RE.match(s)
        if m:
            while stack[-1][0] >= level:
                stack.pop()
            node = {"id": m.group("id"), "type": m.group("type")}
            title = _title_of(m)
            if title is not None:
                node["title"] = title
            parent = stack[-1][1]
            if parent is None:
                root.append(node)
            else:
                parent.setdefault("children", []).append(node)
            stack.append((level, node))
            i += 1
            continue

        m = ATTR_RE.match(s)
        if m:
            while stack[-1][0] >= level:
                stack.pop()
            owner = stack[-1][1]
            if owner is None:
                raise PynSyntaxError(
                    f"属性 {m.group('key')!r} がどのノードにも属していません",
                    line_no, raw)
            owner.setdefault("attrs", {})[m.group("key")] = parse_value(
                m.group("val"), line_no)
            i += 1
            continue

        if NODE_B_RE.match(s):
            raise PynSyntaxError(
                "ノード宣言の末尾に ':' がありません", line_no, raw,
                hint="この文書は投影A(インデント形)です。投影B と混在させることはできません",
            )

        raise PynSyntaxError("解釈できない行です", line_no, raw)

    return {"pyn": "1.0", "root": root}


def _read_prose(lines, start_index: int, indent: int) -> tuple[str, int]:
    """`\"\"\"` 開始行の位置から散文ブロックを読み、(本文, 次の行index) を返す。"""
    body: list[str] = []
    i = start_index + 1
    n = len(lines)
    pad = " " * indent
    while i < n:
        raw = lines[i]
        if raw.strip() == PROSE_DELIM and _leading_spaces(raw) == indent:
            return "\n".join(body), i + 1
        if raw.strip() == "":
            body.append("")
        elif raw.startswith(pad):
            body.append(raw[indent:])
        else:
            raise PynIndentError(
                "散文ブロックの中身が開始位置より浅くインデントされています",
                i + 1, raw,
                hint=f"{indent} 文字以上のインデントが必要です",
            )
        i += 1
    raise PynSyntaxError(
        f"散文ブロックが {PROSE_DELIM} で閉じられていません",
        start_index + 1, lines[start_index])


def _leading_spaces(raw: str) -> int:
    return len(raw) - len(raw.lstrip(" "))


# --------------------------------------------------------------------------
# 投影B: フラット形(インデントに意味がない)
# --------------------------------------------------------------------------

def _parse_flat(lines) -> dict:
    root: list = []
    index: dict[str, dict] = {}
    current: dict | None = None

    i = 0
    n = len(lines)
    while i < n:
        raw = lines[i]
        line_no = i + 1
        if _is_skippable(raw):
            i += 1
            continue
        s = raw.strip()

        if s.startswith(PROSE_DELIM):
            if s != PROSE_DELIM:
                raise PynSyntaxError(
                    f"{PROSE_DELIM} は単独の行に置いてください", line_no, raw)
            if current is None:
                raise PynSyntaxError(
                    "散文ブロックがどのノードにも属していません", line_no, raw)
            block, i = _read_prose(lines, i, _leading_spaces(raw))
            current.setdefault("prose", []).append(block)
            continue

        m = NODE_B_RE.match(s)
        if m and ":" not in s.split('"')[0]:
            node = {"id": m.group("id"), "type": m.group("type")}
            title = _title_of(m)
            if title is not None:
                node["title"] = title
            if node["id"] in index:
                raise PynSyntaxError(
                    f"IDが重複しています: {node['id']}", line_no, raw)
            parent_id = m.group("parent")
            if parent_id is None:
                root.append(node)
            else:
                parent = index.get(parent_id)
                if parent is None:
                    raise PynSyntaxError(
                        f"親ID {parent_id!r} が未定義です", line_no, raw,
                        hint="投影B では親を先に宣言する必要があります(DFS前順)",
                    )
                parent.setdefault("children", []).append(node)
            index[node["id"]] = node
            current = node
            i += 1
            continue

        m = ATTR_RE.match(s)
        if m:
            if current is None:
                raise PynSyntaxError(
                    f"属性 {m.group('key')!r} がどのノードにも属していません",
                    line_no, raw)
            current.setdefault("attrs", {})[m.group("key")] = parse_value(
                m.group("val"), line_no)
            i += 1
            continue

        if NODE_A_RE.match(s):
            raise PynSyntaxError(
                "投影B のノード宣言に ':' は付けません", line_no, raw,
                hint="この文書は投影B(フラット形)です。投影A と混在させることはできません",
            )

        raise PynSyntaxError("解釈できない行です", line_no, raw)

    return {"pyn": "1.0", "root": root}
