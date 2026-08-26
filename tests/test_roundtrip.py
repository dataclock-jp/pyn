"""t-04: 往復プロパティテスト。

cost-2 の担保。
「.pyn は独立した形式ではなく正準形 JSON の可逆な射影である」(s-03)という主張は、
    project(canonicalize(x)) == x
が成り立って初めて成立する。ここが崩れると設計全体が崩れる。

外部依存(hypothesis 等)は使わず、標準ライブラリの random で木を生成する。
"""

from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from pyn import (
    PynIndentError,
    PynRefError,
    PynSyntaxError,
    PynValidationError,
    canonicalize,
    detect_projection,
    dumps,
    edges,
    extract_refs,
    parse,
    render_html,
    to_pyn_a,
    to_pyn_b,
    validate_refs,
)

ROOT = Path(__file__).resolve().parents[1]

TYPES = ["spec", "task", "decision", "section", "note", "constraint", "risk_item"]
TITLES = [
    "認可エンドポイント", "トークン交換", "なぜ PKCE か", "概要",
    "Edge case: a \"quoted\" title", "バックスラッシュ \\ を含む",
    "コロン: とカンマ, を含む", "",
]
WORDS = [
    "SPA では client_secret を秘匿できない。", "**強調**を含む行。",
    "`inline code` はリンク解決の対象外。", "- 箇条書き",
    "移行の順序は固定する。", "| a | b |", "| --- | --- |",
    "```", "code @notaref", "```", "> 引用行",
]
ATTR_KEYS = ["est", "owner", "done", "depends", "priority", "count", "ratio"]


def _rand_scalar(rng: random.Random):
    kind = rng.randrange(7)
    if kind == 0:
        return rng.randrange(-1000, 1000)
    if kind == 1:
        return round(rng.uniform(-100, 100), 3)
    if kind == 2:
        return rng.choice([True, False])
    if kind == 3:
        return None
    if kind == 4:
        return rng.choice(["3d", "alice", "high", "a/b-c.d", "no", "yes", "on"])
    if kind == 5:
        return rng.choice(["", "空白 を 含む", 'quote " あり', "back\\slash",
                           "カンマ, あり", "角括弧 ] あり", "true"])
    return rng.choice(["auth-01", "0", "0.0", "-1"])


def _rand_value(rng: random.Random):
    if rng.random() < 0.25:
        return [_rand_scalar(rng) for _ in range(rng.randrange(0, 4))]
    return _rand_scalar(rng)


def _rand_prose(rng: random.Random, ids: list[str]) -> str:
    lines = []
    for _ in range(rng.randrange(1, 6)):
        line = rng.choice(WORDS)
        if ids and rng.random() < 0.4:
            line += " " + rng.choice(["@", "§"]) + rng.choice(ids)
        lines.append(line)
    return "\n".join(lines)


def _rand_doc(rng: random.Random, n_roots: int = 3) -> dict:
    counter = [0]
    ids: list[str] = []

    def make(depth: int) -> dict:
        counter[0] += 1
        nid = f"n{counter[0]}"
        ids.append(nid)
        node: dict = {"id": nid, "type": rng.choice(TYPES)}
        if rng.random() < 0.8:
            node["title"] = rng.choice(TITLES)
        if rng.random() < 0.7:
            node["attrs"] = {
                rng.choice(ATTR_KEYS): _rand_value(rng)
                for _ in range(rng.randrange(1, 4))
            }
        if rng.random() < 0.5:
            node["prose"] = [
                _rand_prose(rng, ids) for _ in range(rng.randrange(1, 3))
            ]
        if depth < 3 and rng.random() < 0.6:
            node["children"] = [make(depth + 1)
                                for _ in range(rng.randrange(1, 3))]
        return node

    return {"pyn": "1.0", "root": [make(0) for _ in range(n_roots)]}


def docs(count: int = 60):
    for seed in range(count):
        yield seed, canonicalize(_rand_doc(random.Random(seed)))


# --------------------------------------------------------------------------
# 中核のプロパティ
# --------------------------------------------------------------------------

@pytest.mark.parametrize("seed,doc", list(docs()))
def test_roundtrip_projection_a(seed, doc):
    """投影A: parse(to_pyn_a(x)) == x"""
    assert parse(to_pyn_a(doc)) == doc


@pytest.mark.parametrize("seed,doc", list(docs()))
def test_roundtrip_projection_b(seed, doc):
    """投影B: parse(to_pyn_b(x)) == x — インデントに依存しない経路"""
    assert parse(to_pyn_b(doc)) == doc


@pytest.mark.parametrize("seed,doc", list(docs()))
def test_byte_stability(seed, doc):
    """正準 JSON はバイト単位で安定する(t-01 の要件)"""
    assert dumps(parse(to_pyn_a(doc))) == dumps(doc)
    assert dumps(parse(to_pyn_b(doc))) == dumps(doc)


@pytest.mark.parametrize("seed,doc", list(docs()))
def test_cross_projection(seed, doc):
    """A → B → A の相互変換で情報が落ちない(s-01 の可逆性)"""
    via_b = parse(to_pyn_b(parse(to_pyn_a(doc))))
    assert parse(to_pyn_a(via_b)) == doc


@pytest.mark.parametrize("seed,doc", list(docs()))
def test_canonicalize_idempotent(seed, doc):
    assert canonicalize(canonicalize(doc)) == doc


@pytest.mark.parametrize("seed,doc", list(docs()))
def test_formatter_idempotent(seed, doc):
    """同じ文書からは常に同じテキストが出る"""
    a1 = to_pyn_a(doc)
    assert to_pyn_a(parse(a1)) == a1
    b1 = to_pyn_b(doc)
    assert to_pyn_b(parse(b1)) == b1


@pytest.mark.parametrize("seed,doc", list(docs(20)))
def test_html_renders(seed, doc):
    out = render_html(doc, title="t")
    assert out.startswith("<!doctype html>")
    assert out.rstrip().endswith("</html>")


def test_projection_detection():
    doc = canonicalize(_rand_doc(random.Random(0)))
    assert detect_projection(to_pyn_a(doc)) == "A"
    assert detect_projection(to_pyn_b(doc)) == "B"
    assert detect_projection("") is None
    assert detect_projection("# コメントだけ\n") is None


def test_projection_b_declarations_are_unindented():
    """投影B が『インデント破損という故障モードを構造的に持たない』ことの確認。

    ノード宣言・属性行は必ず 0 インデントで出力され、親子は ^parent だけが決める。
    """
    doc = canonicalize(_rand_doc(random.Random(3), n_roots=2))
    text = to_pyn_b(doc)

    in_prose = False
    for line in text.split("\n"):
        if line.strip() == '"""':
            in_prose = not in_prose
            assert line == '"""', "散文の区切りも 0 インデントであること"
            continue
        if in_prose or line == "":
            continue
        assert not line.startswith(" "), f"投影B に構造的インデントがある: {line!r}"

    assert parse(text) == doc


# --------------------------------------------------------------------------
# 空文書・境界
# --------------------------------------------------------------------------

def test_empty_document():
    empty = {"pyn": "1.0", "root": []}
    assert parse("") == empty
    assert parse("# コメントのみ\n\n") == empty
    assert to_pyn_a(empty) == ""
    assert to_pyn_b(empty) == ""


def test_minimal_node():
    doc = parse("task t1:\n")
    assert doc == {"pyn": "1.0", "root": [{"id": "t1", "type": "task"}]}


def test_title_with_escapes():
    doc = {"pyn": "1.0", "root": [
        {"id": "a", "type": "note", "title": 'back\\slash and "quote"'}]}
    assert parse(to_pyn_a(doc)) == canonicalize(doc)


def test_empty_string_value_is_quoted():
    doc = {"pyn": "1.0", "root": [
        {"id": "a", "type": "note", "attrs": {"x": ""}}]}
    text = to_pyn_a(doc)
    assert 'x: ""' in text
    assert parse(text) == canonicalize(doc)


def test_no_yaml_implicit_typing():
    """YAML の暗黙型変換は採用しない(ノルウェー問題を仕様から排除)"""
    doc = parse('note n1:\n  a: no\n  b: yes\n  c: on\n  d: off\n  e: true\n')
    attrs = doc["root"][0]["attrs"]
    assert attrs["a"] == "no"
    assert attrs["b"] == "yes"
    assert attrs["c"] == "on"
    assert attrs["d"] == "off"
    assert attrs["e"] is True


def test_prose_preserves_inner_indentation():
    src = 'note n1:\n  """\n  text\n      indented\n\n  tail\n  """\n'
    doc = parse(src)
    assert doc["root"][0]["prose"] == ["text\n    indented\n\ntail"]
    assert parse(to_pyn_a(doc)) == doc


def test_comments_are_dropped():
    doc = parse("# 先頭コメント\ntask t1:\n  # 途中のコメント\n  est: 3d\n")
    assert doc["root"][0]["attrs"] == {"est": "3d"}


def test_attr_key_order_is_sorted():
    doc = parse("task t1:\n  z: 1\n  a: 2\n  m: 3\n")
    assert list(doc["root"][0]["attrs"]) == ["a", "m", "z"]


# --------------------------------------------------------------------------
# s-02: インライン = グラフの辺
# --------------------------------------------------------------------------

def test_refs_are_derived_not_stored():
    """refs は派生値。表層構文には現れないが往復で再計算される"""
    src = 'decision d1:\n  """\n  @a-01 と §b-02 を参照する。\n  """\n'
    doc = parse(src)
    assert doc["root"][0]["refs"] == ["a-01", "b-02"]
    assert "refs" not in to_pyn_a(doc)
    assert parse(to_pyn_a(doc)) == doc


def test_refs_ignore_code():
    src = ('note n1:\n  """\n  `@notthis` は無視。\n'
           "  ```\n  @northis\n  ```\n  @yesthis は拾う。\n  \"\"\"\n")
    assert parse(src)["root"][0]["refs"] == ["yesthis"]


def test_refs_ignore_email_like():
    src = 'note n1:\n  """\n  user@example.com は参照ではない。\n  """\n'
    assert "refs" not in parse(src)["root"][0]


def test_dangling_ref_is_detected():
    doc = parse('note n1:\n  """\n  @missing を参照。\n  """\n')
    with pytest.raises(PynRefError):
        validate_refs(doc)


def test_valid_ref_passes_and_produces_edge():
    doc = parse('note n1:\n  """\n  @n2 を参照。\n  """\nnote n2:\n')
    validate_refs(doc)
    assert edges(doc) == [("n1", "n2")]


def test_html_resolves_ref_to_title():
    doc = parse('note n1:\n  """\n  @n2 を参照。\n  """\n'
                'note n2 "トークン交換":\n')
    out = render_html(doc, title="t")
    assert '<a class="ref" href="#n2">トークン交換</a>' in out


def test_extract_refs_direct():
    assert extract_refs(["@a b @c @a"]) == ["a", "c"]


# --------------------------------------------------------------------------
# s-01: 破損の検出
# --------------------------------------------------------------------------

def test_tab_indent_rejected():
    with pytest.raises(PynIndentError):
        parse("task t1:\n\test: 3d\n")


def test_inconsistent_indent_rejected():
    with pytest.raises(PynIndentError):
        parse("spec s1:\n  task t1:\n     est: 3d\n")


def test_indent_jump_rejected():
    # 単位は最初の非ゼロインデントで決まるので、飛びは3行目以降で検出される
    with pytest.raises(PynIndentError):
        parse("spec s1:\n  task t1:\n      note n1:\n")


def test_missing_colon_rejected():
    with pytest.raises(PynSyntaxError):
        parse("spec s1:\n  task t1\n")


def test_mixed_projection_rejected():
    with pytest.raises(PynSyntaxError):
        parse("spec s1\ntask t1: \n")


def test_unknown_parent_rejected():
    with pytest.raises(PynSyntaxError):
        parse("spec s1\ntask t1 ^nope\n")


def test_duplicate_id_rejected():
    with pytest.raises(PynValidationError):
        parse("task t1:\ntask t1:\n")


def test_unclosed_prose_rejected():
    with pytest.raises(PynSyntaxError):
        parse('note n1:\n  """\n  本文\n')


def test_nested_array_rejected():
    with pytest.raises(PynSyntaxError):
        parse("task t1:\n  a: [1, [2]]\n")


def test_nonscalar_attr_rejected():
    with pytest.raises(PynValidationError):
        canonicalize({"pyn": "1.0", "root": [
            {"id": "a", "type": "n", "attrs": {"x": {"y": 1}}}]})


def test_bad_id_rejected():
    with pytest.raises(PynValidationError):
        canonicalize({"pyn": "1.0", "root": [{"id": "-bad", "type": "n"}]})


# --------------------------------------------------------------------------
# サンプルと引継ぎ文書(t-08 の先取り)
# --------------------------------------------------------------------------

def test_sample_spec_roundtrips():
    src = (ROOT / "samples" / "auth-spec.pyn").read_text(encoding="utf-8")
    doc = parse(src)
    validate_refs(doc)
    assert parse(to_pyn_a(doc)) == doc
    assert parse(to_pyn_b(doc)) == doc
    assert dumps(parse(to_pyn_b(doc))) == dumps(doc)


def test_handover_json_is_valid_canonical_after_conversion():
    """Handover.json → PYN 正準形 → 各射影 の往復(ドッグフーディング)"""
    from tools.handover_to_pyn import build_doc

    raw = json.loads((ROOT / "Handover.json").read_text(encoding="utf-8"))
    doc = canonicalize(build_doc(raw))
    validate_refs(doc)
    assert parse(to_pyn_a(doc)) == doc
    assert parse(to_pyn_b(doc)) == doc
