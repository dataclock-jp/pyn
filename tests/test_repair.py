"""t-07: 破損検出と修復候補のテスト。

s-01 の主張「ID階層とインデントの冗長性で破損を検出でき、修復候補も出せる」を検証する。
cost-3 に従い、自動適用はしない = diagnose は候補を返すだけであること。
"""

from __future__ import annotations

from pathlib import Path

from pyn import diagnose, report, to_pyn_a, to_pyn_b, parse

ROOT = Path(__file__).resolve().parents[1]


def test_clean_document_has_no_findings():
    src = (ROOT / "samples" / "auth-spec.pyn").read_text(encoding="utf-8")
    assert diagnose(src) == []
    assert report([]).startswith("OK")


def test_generated_projections_are_clean():
    doc = parse((ROOT / "samples" / "auth-spec.pyn").read_text(encoding="utf-8"))
    assert diagnose(to_pyn_a(doc)) == []
    assert diagnose(to_pyn_b(doc)) == []


def test_detects_non_multiple_indent():
    broken = 'spec s1:\n  task t1:\n     est: 3d\n'
    findings = diagnose(broken)
    assert len(findings) == 1
    assert findings[0].line_no == 3
    assert "倍数ではありません" in findings[0].problem
    assert findings[0].candidates


def test_detects_indent_jump():
    broken = 'spec s1:\n  task t1:\n        note n1:\n'
    findings = diagnose(broken)
    assert any("飛んでいます" in f.problem for f in findings)


def test_detects_tab_indent():
    findings = diagnose("task t1:\n\test: 3d\n")
    assert len(findings) == 1
    assert "タブ" in findings[0].problem


def test_id_naming_gives_priority_candidate():
    """`cost-1` は `cost` の子である、という命名の冗長性を使う"""
    broken = 'constraint cost:\n  item cost-1 "A":\n      item cost-2 "B":\n'
    findings = diagnose(broken)
    assert findings
    top = findings[0].candidates[0]
    assert "命名" in top.reason
    assert top.parent_id == "cost"


def test_candidates_never_exceed_syntactic_maximum():
    broken = 'spec s1:\n          task t1:\n'
    for f in diagnose(broken):
        for c in f.candidates:
            assert c.level >= 0


def test_diagnose_does_not_modify_input():
    """cost-3: 自動適用しない。診断は純粋関数である。"""
    broken = 'spec s1:\n  task t1:\n     est: 3d\n'
    before = broken
    diagnose(broken)
    assert broken == before


def test_report_mentions_no_autofix():
    findings = diagnose('spec s1:\n  task t1:\n     est: 3d\n')
    assert "自動適用はしません" in report(findings)


def test_prose_content_is_not_diagnosed():
    """散文の中身は自由なインデントを許す(構造ではないため)"""
    src = 'note n1:\n  """\n  text\n       deeply indented sample\n  """\n'
    assert diagnose(src) == []
