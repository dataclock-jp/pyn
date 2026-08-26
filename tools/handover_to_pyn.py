"""Handover.json → PYN 正準形 → Handover.md / .pyn(投影A)/ .html。

ドッグフーディング。
Handover.json は「型とIDを持つノードの集合と、その間の辺」として書かれているので、
PYN の正準形へほぼ機械的に写せる。写した後は t-06 のレンダラがそのまま使える。

d-00 の原則に従い、Handover.md / .pyn / .html は常にここから生成する。手で編集しない。
(t-13 以前は .md だけが手書きで、その状態自体が d-00 違反だった)

    python -m tools.handover_to_pyn
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pyn import (  # noqa: E402
    canonicalize,
    dumps,
    render_html,
    render_md,
    to_pyn_a,
    validate_refs,
)

ROOT = Path(__file__).resolve().parents[1]

SCALAR = (str, int, float, bool, type(None))


# --------------------------------------------------------------------------
# 小道具
# --------------------------------------------------------------------------

def _node(nid, ntype, title=None, attrs=None, prose=None, children=None):
    n = {"id": nid, "type": ntype}
    if title:
        n["title"] = title
    if attrs:
        n["attrs"] = {k: v for k, v in attrs.items() if v is not None}
    if prose:
        n["prose"] = [p for p in prose if p and p.strip()]
    if children:
        n["children"] = children
    return n


def _labelled(label: str, value) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, list):
        body = "\n".join(f"- {v}" for v in value)
        return f"**{label}**\n\n{body}"
    return f"**{label}** — {value}"


def _kv_bullets(label: str, mapping: dict) -> str | None:
    if not mapping:
        return None
    lines = [f"**{label}**", ""]
    lines += [f"- `{k}` — {v}" for k, v in mapping.items()]
    return "\n".join(lines)


def _fence(label: str, body: str) -> str:
    return f"**{label}**\n\n```\n{body}\n```"


def _refs_line(refs) -> str | None:
    if not refs:
        return None
    return "関連: " + " ".join(f"@{r}" for r in refs)


# --------------------------------------------------------------------------
# 変換本体
# --------------------------------------------------------------------------

def _convert_design_node(src: dict) -> dict:
    prose: list[str] = []
    attrs: dict = {}

    for key in ("conclusion", "problem", "bad_compromise", "key_insight",
                "relation_to_d00", "note", "rationale", "rejected", "detail"):
        label = {
            "conclusion": "結論",
            "problem": "問題",
            "bad_compromise": "安易な妥協",
            "key_insight": "要点",
            "relation_to_d00": "d-00 との関係",
            "note": "補足",
            "rationale": "根拠",
            "rejected": "見送った案",
            "detail": "詳細",
        }[key]
        text = _labelled(label, src.get(key))
        if text:
            prose.append(text)

    if isinstance(src.get("syntax"), dict):
        prose.append(_kv_bullets("構文", src["syntax"]))
    if isinstance(src.get("mechanism"), dict):
        prose.append(_kv_bullets("仕組み", src["mechanism"]))

    for key, label in (("example_A", "投影A(インデント形)"),
                       ("example_B", "投影B(フラット形)")):
        if src.get(key):
            prose.append(_fence(label, src[key]))

    if src.get("rule"):
        prose.append(f"> {src['rule']}")

    ref_line = _refs_line(src.get("refs"))
    if ref_line:
        prose.append(ref_line)

    children = []
    for item in src.get("items", []) or []:
        children.append(_node(
            item["id"], "item", item.get("title"),
            attrs={"mitigation": item.get("mitigation")},
            prose=[_labelled("詳細", item.get("detail") or item.get("note"))],
        ))

    for key in ("status", "priority"):
        if isinstance(src.get(key), SCALAR) and src.get(key) is not None:
            attrs[key] = src[key]

    return _node(src["id"], src["type"], src.get("title"),
                 attrs=attrs, prose=prose, children=children)


def _convert_task(src: dict) -> dict:
    return _node(
        src["id"], "task", src.get("title"),
        attrs={
            "status": src.get("status"),
            "est": src.get("est"),
            "depends": src.get("depends") or None,
            "priority": src.get("priority"),
        },
        prose=[
            src.get("detail"),
            _labelled("結果", src.get("result")),
        ],
    )


def _convert_question(src: dict) -> dict:
    prose = [
        _labelled("問い", src.get("question")),
        _labelled("背景", src.get("context")),
        _labelled("推奨", src.get("recommendation")),
        _labelled("決定", src.get("resolution")),
    ]
    return _node(
        src["id"], "question", src.get("question"),
        attrs={
            "status": src.get("status"),
            "default_if_no_answer": src.get("default_if_no_answer"),
        },
        prose=prose,
    )


def _convert_readme(src: dict) -> dict:
    """readme セクション(手書きの読み物)をノードへ写す。

    本文は Markdown のまま prose に入る。見出しはノードの深さが持つので
    本文側には書けない(q-03)。ゆえに節を分けたければ children にする。
    """
    return _node(
        src["id"], src.get("type", "section"), src.get("title"),
        prose=[src.get("body")],
        children=[_convert_readme(c) for c in src.get("children", []) or []],
    )


def build_doc(raw: dict) -> dict:
    """Handover.json の生 dict を PYN 正準形へ写す。"""
    root: list[dict] = []

    meta = raw.get("meta", {})
    root.append(_node(
        "meta", "meta", "この文書について",
        attrs={k: v for k, v in meta.items() if isinstance(v, SCALAR)},
        prose=[raw.get("$comment")],
    ))

    for section in raw.get("readme", []) or []:
        root.append(_convert_readme(section))

    goal = raw.get("goal")
    if goal:
        root.append(_node(
            goal["id"], goal.get("type", "goal"), goal.get("title"),
            prose=[_labelled("完了条件", goal.get("success_criteria"))],
        ))

    for src in raw.get("nodes", []):
        root.append(_convert_design_node(src))

    tasks = raw.get("tasks") or []
    if tasks:
        root.append(_node(
            "plan", "plan", "実装計画",
            prose=["`status` が `todo` のものを上から実行する。"
                   "依存は `depends` を参照。"],
            children=[_convert_task(t) for t in tasks],
        ))

    questions = raw.get("open_questions") or []
    if questions:
        root.append(_node(
            "questions", "questions", "未解決の問い",
            children=[_convert_question(q) for q in questions],
        ))

    actions = raw.get("next_actions") or []
    if actions:
        root.append(_node(
            "actions", "actions", "次にやること",
            prose=[_labelled("手順", actions)],
        ))

    artifacts = raw.get("artifacts") or {}
    if artifacts:
        root.append(_node(
            "artifacts", "artifacts", "成果物",
            prose=[
                _labelled("既存", artifacts.get("existing")),
                _labelled("予定", artifacts.get("planned")),
            ],
        ))

    return {"pyn": "1.0", "root": root}


# --------------------------------------------------------------------------

def main(argv=None) -> int:
    src = ROOT / "Handover.json"
    raw = json.loads(src.read_text(encoding="utf-8"))

    doc = canonicalize(build_doc(raw))
    validate_refs(doc)

    (ROOT / "Handover.pyn").write_text(to_pyn_a(doc), encoding="utf-8")
    (ROOT / "build" / "Handover.canonical.json").parent.mkdir(exist_ok=True)
    (ROOT / "build" / "Handover.canonical.json").write_text(
        dumps(doc), encoding="utf-8")

    meta = raw.get("meta", {})
    html = render_html(
        doc,
        title="引継ぎ — Synthesis from HTML and JSON",
        subtitle=f"{meta.get('project', '')} / 最終更新 {meta.get('last_session', '')}",
        note="このページは Handover.json から生成されています(手で編集しないこと)。"
             " 生成: python -m tools.handover_to_pyn",
    )
    (ROOT / "Handover.html").write_text(html, encoding="utf-8")

    md = render_md(
        doc,
        title="引継ぎ — Synthesis from HTML and JSON",
        subtitle=f"{meta.get('project', '')} / 最終更新 {meta.get('last_session', '')}",
        note="このファイルは Handover.json から生成されています(手で編集しないこと)。"
             " 生成: `python -m tools.handover_to_pyn`",
    )
    (ROOT / "Handover.md").write_text(md, encoding="utf-8")

    n_nodes = sum(1 for _ in _walk(doc))
    print(f"OK  nodes={n_nodes}")
    print("  Handover.md                    (Markdown)")
    print("  Handover.pyn                   (投影A)")
    print("  build/Handover.canonical.json  (正準形)")
    print("  Handover.html                  (HTML)")
    return 0


def _walk(doc):
    stack = list(doc["root"])
    while stack:
        n = stack.pop()
        yield n
        stack.extend(n.get("children", []))


if __name__ == "__main__":
    raise SystemExit(main())
