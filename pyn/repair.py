"""t-07: インデント破損の検出と修復候補の提示。

s-01 の約束の後半。
    ID階層とインデントは2系統の冗長性を成すので、破損を「検出でき、修復候補も出せる」。

cost-3 の制約:
    自動修復は誤修復しうる。したがってこのモジュールは **決して自動適用しない**。
    候補を提示するだけで、選ぶのは人間。

冗長性の出どころは2つ:
  1. 構文上の制約   — インデント幅は一定単位の倍数、深さは一度に1段しか増えない
  2. ID の命名階層 — `cost-1` は `cost` の子、`auth-01` は `auth` の子に見える
2 は規約であって仕様ではないため、候補の *優先順位付け* にのみ使う。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .parser import ATTR_RE, NODE_A_RE, NODE_B_RE, _is_skippable
from .model import PROSE_DELIM


@dataclass
class Candidate:
    indent: int
    level: int
    parent_id: str | None
    reason: str

    def describe(self) -> str:
        where = f"{self.parent_id} の子" if self.parent_id else "ルート直下"
        return f"インデント {self.indent}({where}) — {self.reason}"


@dataclass
class Finding:
    line_no: int
    line: str
    problem: str
    candidates: list[Candidate] = field(default_factory=list)

    def describe(self) -> str:
        out = [f"line {self.line_no}: {self.problem}", f"  | {self.line}"]
        if self.candidates:
            out.append("  修復候補:")
            for i, c in enumerate(self.candidates, 1):
                out.append(f"    {i}. {c.describe()}")
        else:
            out.append("  修復候補: なし(手で直してください)")
        return "\n".join(out)


def _id_of(line: str) -> str | None:
    s = line.strip()
    for rx in (NODE_A_RE, NODE_B_RE):
        m = rx.match(s)
        if m:
            return m.group("id")
    return None


def _is_descendant_name(child_id: str, ancestor_id: str) -> bool:
    """`cost-1` ⊂ `cost`、`auth.sub` ⊂ `auth` のような命名上の包含。"""
    return any(child_id.startswith(ancestor_id + sep) for sep in ("-", ".", "_"))


def diagnose(text: str) -> list[Finding]:
    """投影A のテキストからインデント破損を検出する。パースは通らなくてよい。"""
    text = text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")

    findings: list[Finding] = []
    unit: int | None = None
    # 構文的に妥当だった行から積むスタック: (level, id or None)
    stack: list[tuple[int, str | None]] = [(-1, None)]
    in_prose = False
    prose_indent = 0

    for i, raw in enumerate(lines):
        line_no = i + 1

        if in_prose:
            if raw.strip() == PROSE_DELIM and _leading(raw) == prose_indent:
                in_prose = False
            continue

        if _is_skippable(raw):
            continue

        if "\t" in raw[: len(raw) - len(raw.lstrip())]:
            findings.append(Finding(
                line_no, raw,
                "タブによるインデント(スペースと混在すると検出不能な破損になる)",
                [Candidate(0, 0, None, "タブを半角スペースへ置換してください")],
            ))
            continue

        indent = _leading(raw)
        s = raw.strip()

        if s.startswith(PROSE_DELIM):
            in_prose = True
            prose_indent = indent
            continue

        is_node = bool(NODE_A_RE.match(s))
        is_attr = bool(ATTR_RE.match(s)) and not is_node
        if not (is_node or is_attr):
            continue

        if unit is None and indent > 0:
            unit = indent

        u = unit or 2
        node_id = _id_of(raw) if is_node else None

        if indent % u != 0:
            findings.append(Finding(
                line_no, raw,
                f"インデント {indent} が単位 {u} の倍数ではありません",
                _candidates(indent, u, stack, node_id, snap=True),
            ))
            indent = (indent // u) * u  # 以降の解析は最寄りの妥当値で続行

        level = indent // u
        current = stack[-1][0]
        if level > current + 1:
            findings.append(Finding(
                line_no, raw,
                f"インデントが飛んでいます(現在の深さ {current} に対して {level})",
                _candidates(indent, u, stack, node_id, snap=False),
            ))
            level = current + 1
            indent = level * u

        while stack[-1][0] >= level:
            stack.pop()
        if is_node:
            stack.append((level, node_id))

    return findings


def _candidates(indent, unit, stack, node_id, *, snap) -> list[Candidate]:
    out: list[Candidate] = []
    seen: set[int] = set()

    def add(target_indent: int, reason: str, front: bool = False):
        target_indent = max(0, target_indent)
        if target_indent in seen:
            return
        seen.add(target_indent)
        level = target_indent // unit
        parent = None
        for lv, nid in reversed(stack):
            if lv < level:
                parent = nid
                break
        c = Candidate(target_indent, level, parent, reason)
        out.insert(0, c) if front else out.append(c)

    max_indent = (stack[-1][0] + 1) * unit

    if snap:
        add((indent // unit) * unit, f"最寄りの妥当なインデント(切り捨て)")
        add(((indent // unit) + 1) * unit, "最寄りの妥当なインデント(切り上げ)")
    add(max_indent, "構文上ここまでしか深くできない")

    # ID の命名階層による優先候補(規約ベースなので最優先で提示する)
    if node_id:
        for lv, nid in reversed(stack):
            if nid and _is_descendant_name(node_id, nid):
                add((lv + 1) * unit,
                    f"ID の命名から {nid} の子と推定される", front=True)
                break

    return [c for c in out if c.indent <= max_indent] or out[:1]


def _leading(raw: str) -> int:
    return len(raw) - len(raw.lstrip(" \t"))


def report(findings: list[Finding]) -> str:
    if not findings:
        return "OK  インデント破損は検出されませんでした"
    head = f"{len(findings)} 件のインデント破損を検出しました" \
           "(cost-3 により自動適用はしません)"
    return "\n\n".join([head] + [f.describe() for f in findings])
