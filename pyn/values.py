"""スカラー/配列値の表記。parse と emit を同じモジュールに置き、対称性を構造的に保証する。

設計上の決定(d-01 / s-03):
  * YAML の暗黙型変換は採用しない。``no`` ``yes`` ``on`` ``off`` は文字列のまま。
    真偽値は ``true`` / ``false`` / ``null`` のみ。いわゆる「ノルウェー問題」を仕様から排除する。
  * 裸の文字列を許すのは「parse し直して同じ文字列に戻る」場合のみ。
    それ以外は必ず引用する(is_bare_safe が自己検査で判定する)。
"""

from __future__ import annotations

import re

from .errors import PynSyntaxError

INT_RE = re.compile(r"^-?(?:0|[1-9][0-9]*)$")
FLOAT_RE = re.compile(r"^-?(?:0|[1-9][0-9]*)\.[0-9]+$")
BARE_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_./-]*$")

RESERVED = {"true", "false", "null"}


# --------------------------------------------------------------------------
# parse
# --------------------------------------------------------------------------

def parse_value(text: str, line_no: int | None = None):
    s = text.strip()
    if s == "":
        raise PynSyntaxError("値が空です", line_no, text,
                             hint='空文字列を表したい場合は "" と書いてください')
    if s.startswith("[") and s.endswith("]"):
        return [parse_scalar(part, line_no) for part in _split_list(s[1:-1], line_no)]
    return parse_scalar(s, line_no)


def parse_scalar(text: str, line_no: int | None = None):
    s = text.strip()
    if s == "":
        raise PynSyntaxError("値が空です", line_no, text)
    if s[0] == '"':
        if len(s) < 2 or s[-1] != '"':
            raise PynSyntaxError("引用符が閉じていません", line_no, text)
        return _unescape(s[1:-1], line_no, text)
    if s == "true":
        return True
    if s == "false":
        return False
    if s == "null":
        return None
    if INT_RE.match(s):
        return int(s)
    if FLOAT_RE.match(s):
        return float(s)
    if not BARE_RE.match(s):
        raise PynSyntaxError(
            f"裸の値として解釈できません: {s!r}", line_no, text,
            hint='引用符で囲ってください(例: "…")',
        )
    return s


def _split_list(inner: str, line_no: int | None):
    parts: list[str] = []
    buf: list[str] = []
    in_quote = False
    escaped = False
    for ch in inner:
        if escaped:
            buf.append(ch)
            escaped = False
            continue
        if in_quote:
            if ch == "\\":
                buf.append(ch)
                escaped = True
            elif ch == '"':
                in_quote = False
                buf.append(ch)
            else:
                buf.append(ch)
            continue
        if ch == '"':
            in_quote = True
            buf.append(ch)
        elif ch == ",":
            parts.append("".join(buf))
            buf = []
        elif ch == "[":
            raise PynSyntaxError("配列は入れ子にできません", line_no, inner,
                                 hint="階層は子ノードで表現してください")
        else:
            buf.append(ch)
    if in_quote:
        raise PynSyntaxError("配列内の引用符が閉じていません", line_no, inner)
    tail = "".join(buf)
    if parts or tail.strip():
        parts.append(tail)
    return [p for p in parts if p.strip() != ""] if len(parts) else []


def _unescape(body: str, line_no: int | None, ctx: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(body):
        ch = body[i]
        if ch == "\\":
            if i + 1 >= len(body):
                raise PynSyntaxError("エスケープが不完全です", line_no, ctx)
            nxt = body[i + 1]
            if nxt == "n":
                out.append("\n")
            elif nxt == "t":
                out.append("\t")
            elif nxt in ('"', "\\"):
                out.append(nxt)
            else:
                raise PynSyntaxError(f"未知のエスケープ: \\{nxt}", line_no, ctx)
            i += 2
            continue
        if ch == '"':
            raise PynSyntaxError("引用符が値の途中で閉じています", line_no, ctx)
        out.append(ch)
        i += 1
    return "".join(out)


# --------------------------------------------------------------------------
# emit
# --------------------------------------------------------------------------

def is_bare_safe(s: str) -> bool:
    """裸で書けるのは「書いて読み直すと同じものに戻る」文字列だけ、という自己検査。"""
    if s in RESERVED or not BARE_RE.match(s):
        return False
    try:
        return parse_scalar(s) == s
    except PynSyntaxError:
        return False


def quote(s: str) -> str:
    body = (
        s.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\t", "\\t")
    )
    return f'"{body}"'


def emit_scalar(value) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        text = repr(value)
        # 正準形では指数表記を使わない(FLOAT_RE で読み戻せる形に限る)
        if not FLOAT_RE.match(text):
            text = f"{value:.10f}".rstrip("0")
            if text.endswith("."):
                text += "0"
        return text
    if isinstance(value, str):
        return value if is_bare_safe(value) else quote(value)
    raise TypeError(f"スカラーではありません: {type(value).__name__}")


def emit_value(value) -> str:
    if isinstance(value, list):
        return "[" + ", ".join(emit_scalar(v) for v in value) + "]"
    return emit_scalar(value)
