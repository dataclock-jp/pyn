"""d-00 の「生成物を手で編集しない」を CI 側から強制する(t-13)。

Handover.md が手書きに戻った瞬間、二重管理が復活する。
それを人間の規律ではなくテストで止めるのがここ。
"""

from __future__ import annotations

import json
from pathlib import Path

from pyn import canonicalize, dumps, render_md, to_pyn_a, validate_refs
from tools.handover_to_pyn import build_doc

ROOT = Path(__file__).resolve().parents[1]


def _doc():
    raw = json.loads((ROOT / "Handover.json").read_text(encoding="utf-8"))
    return canonicalize(build_doc(raw)), raw


def test_handover_json_converts_and_refs_resolve():
    doc, _ = _doc()
    validate_refs(doc)


def test_generated_md_is_up_to_date():
    doc, raw = _doc()
    meta = raw.get("meta", {})
    expected = render_md(
        doc,
        title="引継ぎ — Synthesis from HTML and JSON",
        subtitle=f"{meta.get('project', '')} / 最終更新 {meta.get('last_session', '')}",
        note="このファイルは Handover.json から生成されています(手で編集しないこと)。"
             " 生成: `python -m tools.handover_to_pyn`",
    )
    actual = (ROOT / "Handover.md").read_text(encoding="utf-8")
    assert actual == expected, (
        "Handover.md が Handover.json と一致しません。"
        "手で編集せず python -m tools.handover_to_pyn で再生成してください(d-00)"
    )


def test_generated_pyn_is_up_to_date():
    doc, _ = _doc()
    assert (ROOT / "Handover.pyn").read_text(encoding="utf-8") == to_pyn_a(doc)


def test_generated_canonical_json_is_up_to_date():
    doc, _ = _doc()
    path = ROOT / "build" / "Handover.canonical.json"
    assert path.read_text(encoding="utf-8") == dumps(doc)
