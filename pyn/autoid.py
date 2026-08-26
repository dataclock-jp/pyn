"""t-17: ID の自動採番(cost-1 の mitigation)。

cost-1 は「全ノードに一意な ID を振る規律が必須」で、そこに
「ID自動採番で緩和する」と書いてあった。その実装。

設計上の判断が 3 つある。

1. **テキストのまま処理する。** 正準形へ写して吐き直すと、コメントも空行も
   書き手の並びも失われる(d-02 / t-14)。採番は「足りない ID を書き足すだけ」の
   作業なので、他の一切を触らないほうが正しい。ゆえに行ベースで処理する。

2. **ID を持つ宣言には絶対に触らない。** ID の価値は時間をまたいで安定していること。
   振り直しは `@参照` も過去のパッチ手順も壊す。採番は *一度だけ* である。

3. **採番規則は「親の ID + 連番」。** タイトルからスラッグを作る方式は日本語で
   破綻する(「認可エンドポイント」から使える ID は作れない)。親の ID を接頭辞に
   持たせると命名の階層が残り、`repair.py` の修復候補ヒューリスティクスも生きる。

ドラフト構文は **タイトル必須**:

    task "認可エンドポイント":        ← 採番対象
    task auth-01 "認可エンドポイント": ← すでに ID があるので触らない

タイトルを必須にしているのは、属性行 ``key:`` と見分けるため。
ドラフト側は必ず引用符が閉じてからコロンが来るので、取り違えようがない。
"""

from __future__ import annotations

import re

from .errors import PynSyntaxError
from .model import PROSE_DELIM

#: ID を持たないノード宣言(投影A)。タイトル必須。
DRAFT_RE = re.compile(
    r'^(?P<indent>[ ]*)'
    r'(?P<type>[A-Za-z_][A-Za-z0-9_-]*)'
    r'[ ]+(?P<title>"(?:[^"\\]|\\.)*")'
    r'[ ]*:[ ]*$'
)

#: ID を持つノード宣言(投影A)。採番の親を知るために使う。
FULL_RE = re.compile(
    r'^(?P<indent>[ ]*)'
    r'(?P<type>[A-Za-z_][A-Za-z0-9_-]*)'
    r'[ ]+(?P<id>[A-Za-z0-9_][A-Za-z0-9_.-]*)'
    r'(?:[ ]+"(?:[^"\\]|\\.)*")?'
    r'[ ]*:[ ]*$'
)


def assign_ids(text: str) -> tuple[str, list[tuple[int, str]]]:
    """ID の無い宣言に ID を振る。戻り値は (新しいテキスト, [(行番号, 振った ID)])。

    ID を持つ行・属性行・散文・コメント・空行は 1 バイトも変えない。
    二度実行しても結果が変わらないこと(冪等)をテストで保証している。
    """
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")

    taken = {m.group("id") for m in
             (FULL_RE.match(line) for line in _outside_prose(lines)) if m}

    out: list[str] = []
    assigned: list[tuple[int, str]] = []
    stack: list[tuple[int, str]] = []  # (インデント, ID)
    prose_indent: int | None = None

    for i, line in enumerate(lines):
        stripped = line.strip()

        if prose_indent is not None:
            if stripped == PROSE_DELIM and _indent_of(line) == prose_indent:
                prose_indent = None
            out.append(line)
            continue
        if stripped == PROSE_DELIM:
            prose_indent = _indent_of(line)
            out.append(line)
            continue

        full = FULL_RE.match(line)
        draft = DRAFT_RE.match(line)
        if not (full or draft):
            out.append(line)
            continue

        indent = len((full or draft).group("indent"))
        while stack and stack[-1][0] >= indent:
            stack.pop()

        if full:
            stack.append((indent, full.group("id")))
            out.append(line)
            continue

        parent = stack[-1][1] if stack else None
        new_id = _next_id(parent, draft.group("type"), taken)
        taken.add(new_id)
        stack.append((indent, new_id))
        assigned.append((i + 1, new_id))
        out.append(
            f'{draft.group("indent")}{draft.group("type")} {new_id}'
            f' {draft.group("title")}:'
        )

    return "\n".join(out), assigned


def _outside_prose(lines):
    prose_indent: int | None = None
    for line in lines:
        stripped = line.strip()
        if prose_indent is not None:
            if stripped == PROSE_DELIM and _indent_of(line) == prose_indent:
                prose_indent = None
            continue
        if stripped == PROSE_DELIM:
            prose_indent = _indent_of(line)
            continue
        yield line


def _indent_of(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _next_id(parent: str | None, node_type: str, taken: set[str]) -> str:
    """親の ID + 連番。ルート直下は型 + 連番。

    既存 ID との衝突は避けるが、**空いた番号を埋め直さない**(単調増加)。
    番号を再利用すると、削除されたノードの ID が別の意味で復活してしまう。
    """
    prefix = f"{parent}." if parent else f"{node_type}-"
    used = [int(m.group(1)) for m in
            (re.fullmatch(re.escape(prefix) + r"(\d+)", t) for t in taken) if m]
    return f"{prefix}{max(used) + 1 if used else 1}"


def check_assignable(text: str) -> None:
    """採番しても strict なパーサを通る形かを事前に確かめる(壊れた入力を弾く)。"""
    from .parser import parse

    assigned_text, _ = assign_ids(text)
    try:
        parse(assigned_text)
    except PynSyntaxError as exc:
        raise PynSyntaxError(
            f"採番しても構文として成立しません: {exc}",
            hint="ドラフト構文は `type \"title\":`(タイトル必須)です",
        ) from exc
