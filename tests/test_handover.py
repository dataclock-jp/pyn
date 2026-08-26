"""d-00 の「生成物を手で編集しない」を CI 側から強制する(t-13 / t-16)。

t-16 で正本が Handover.json(独自形)から Handover.pyn(正準形)へ反転した。
そのため「変換器の出力が正しいか」を見る必要はもうなく、
見るべきは **正本と生成物がずれていないか** だけになった。

期待値は tools.handover_to_pyn.outputs() から取る。タイトルや注記の文字列を
テスト側にも書くと、それこそ二重管理になるため。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pyn import parse, validate_refs
from tools.handover_to_pyn import SOURCE, load, outputs

ROOT = Path(__file__).resolve().parents[1]


def test_source_is_the_pyn_file():
    """正本は Handover.pyn。独自形の Handover.json は t-16 で役目を終えた。"""
    assert SOURCE == ROOT / "Handover.pyn"
    assert not (ROOT / "Handover.json").exists(), (
        "Handover.json が復活している。正本は Handover.pyn ひとつ(t-16)"
    )


def test_source_parses_and_refs_resolve():
    validate_refs(parse(SOURCE.read_text(encoding="utf-8")))


@pytest.mark.parametrize("name", ["Handover.md", "Handover.html",
                                  "build/Handover.canonical.json"])
def test_generated_files_are_up_to_date(name):
    produced = {p.relative_to(ROOT).as_posix(): text
                for p, text in outputs(load()).items()}
    path = ROOT / name
    assert path.read_text(encoding="utf-8") == produced[name], (
        f"{name} が Handover.pyn に追従していません。"
        "手で編集せず python -m tools.handover_to_pyn で再生成してください(d-00)"
    )


def test_check_mode_agrees():
    from tools.handover_to_pyn import main

    assert main(["--check"]) == 0


def test_semantics_live_in_typed_nodes_not_in_prose_labels():
    """t-16 の案A。結論や根拠は太字ラベルではなく型付きノードであること。"""
    from pyn import index_by_id

    index = index_by_id(load())
    assert index["d-00.conclusion"]["type"] == "conclusion"
    assert index["d-00.rationale"]["type"] == "rationale"
    assert index["t-01"]["attrs"]["status"] == "done"
    # 「**結論** —」のような、意味を散文へ埋める書き方に戻っていないこと
    for node in index.values():
        for block in node.get("prose", []):
            assert not block.startswith("**結論**"), node["id"]
