"""t-09: VS Code の TextMate 文法を、実物の .pyn に対して検証する。

TextMate エンジン(Oniguruma)は動かせないので、文法そのものは走らせられない。
代わりに次を確かめる:

  * 文法が JSON として妥当で、拡張の manifest と scopeName が食い違っていない
  * すべてのパターンが正規表現として成立する(Python の re で読める形に保つ)
  * **実物の samples/auth-spec.pyn の各行が、想定どおりのパターンに拾われる**

3 つめが本体である。文法が実装とずれていれば、サンプルのどれかの行が
「どのパターンにも当たらない」か「別のパターンに当たる」ので、そこで落ちる。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXT = ROOT / "editors" / "vscode"
GRAMMAR_PATH = EXT / "syntaxes" / "pyn.tmLanguage.json"


@pytest.fixture(scope="module")
def grammar():
    return json.loads(GRAMMAR_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def repo(grammar):
    return grammar["repository"]


def _compile(pattern: str):
    """TextMate の正規表現を Python の re へ。lookbehind と \\G は使っていない前提。"""
    return re.compile(pattern)


def _rules(grammar):
    """repository の全ルールを (名前, ルール) で列挙する。"""
    return grammar["repository"].items()


# --------------------------------------------------------------------------
# manifest との整合
# --------------------------------------------------------------------------

def test_manifest_points_at_the_grammar():
    manifest = json.loads((EXT / "package.json").read_text(encoding="utf-8"))
    contributes = manifest["contributes"]
    assert contributes["languages"][0]["extensions"] == [".pyn"]
    grammar_entry = contributes["grammars"][0]
    assert grammar_entry["language"] == contributes["languages"][0]["id"]
    assert (EXT / grammar_entry["path"]).resolve() == GRAMMAR_PATH.resolve()

    grammar = json.loads(GRAMMAR_PATH.read_text(encoding="utf-8"))
    assert grammar["scopeName"] == grammar_entry["scopeName"]


def test_language_configuration_is_referenced():
    manifest = json.loads((EXT / "package.json").read_text(encoding="utf-8"))
    path = EXT / manifest["contributes"]["languages"][0]["configuration"]
    assert path.exists()
    config = json.loads(path.read_text(encoding="utf-8"))
    assert config["comments"]["lineComment"] == "#"
    for key in ("increaseIndentPattern", "decreaseIndentPattern"):
        _compile(config["indentationRules"][key])


def test_all_patterns_compile(grammar):
    for name, rule in _rules(grammar):
        for key in ("match", "begin", "end"):
            if key in rule:
                _compile(rule[key])
        for sub in rule.get("patterns", []):
            if "match" in sub:
                _compile(sub["match"])


def test_top_level_includes_exist(grammar):
    for entry in grammar["patterns"]:
        assert entry["include"].lstrip("#") in grammar["repository"]


def test_every_repository_rule_is_reachable(grammar):
    """孤立したルールを残さない(消し忘れは文法の腐りどころ)。"""
    referenced = {e["include"].lstrip("#") for e in grammar["patterns"]}
    for _, rule in _rules(grammar):
        for sub in rule.get("patterns", []):
            if "include" in sub:
                referenced.add(sub["include"].lstrip("#"))
    assert set(grammar["repository"]) == referenced


# --------------------------------------------------------------------------
# 実物の行を拾えること
# --------------------------------------------------------------------------

def test_node_declaration_projection_a(repo):
    m = _compile(repo["node"]["match"]).match('  task auth-01 "認可エンドポイント実装":')
    assert m
    assert m.group(1) == "task"
    assert m.group(2) == "auth-01"
    assert m.group(3) == '"認可エンドポイント実装"'
    assert m.group(4) == ":"


def test_node_declaration_without_title(repo):
    m = _compile(repo["node"]["match"]).match("spec auth:")
    assert m and m.group(2) == "auth" and m.group(3) is None


def test_node_declaration_projection_b(repo):
    m = _compile(repo["node"]["match"]).match('task auth-02 "トークン交換" ^auth')
    assert m
    assert m.group(5) == "^"
    assert m.group(6) == "auth"


def test_node_declaration_with_escaped_quote_in_title(repo):
    m = _compile(repo["node"]["match"]).match('note n1 "a \\"b\\" c":')
    assert m and m.group(3) == '"a \\"b\\" c"'


def test_attribute_and_values(repo):
    begin = _compile(repo["attribute"]["begin"])
    assert begin.match("  est: 3d")
    assert begin.match('    chosen: "OAuth2 + PKCE"')
    assert not begin.match("  # コメント")


@pytest.mark.parametrize("text", ["true", "false", "null"])
def test_constants(repo, text):
    assert _compile(repo["constant"]["match"]).fullmatch(text)


@pytest.mark.parametrize("text", ["no", "yes", "on", "off", "True", "nullish"])
def test_yaml_truthy_words_are_not_constants(repo, text):
    """YAML の暗黙型変換を採用しない、という決定を文法側でも守る。"""
    assert not _compile(repo["constant"]["match"]).search(text)


@pytest.mark.parametrize("text", ["3", "-1", "1.2", "0", "0.5"])
def test_numbers(repo, text):
    assert _compile(repo["number"]["match"]).fullmatch(text)


@pytest.mark.parametrize("text", ["3d", "a/b-c.d", "1.2.3"])
def test_bare_strings_are_not_numbers(repo, text):
    assert not _compile(repo["number"]["match"]).fullmatch(text)


def test_reference(repo):
    m = _compile(repo["reference"]["match"]).search("移行は @migration に従う")
    assert m and m.group(2) == "migration"


def test_reference_ignores_email_like(repo):
    assert not _compile(repo["reference"]["match"]).search("alice@example.invalid")


def test_comment(repo):
    assert _compile(repo["comment"]["match"]).match("  # なぜこうしたか")
    assert not _compile(repo["comment"]["match"]).match("  est: 3d")


def test_prose_delimiters(repo):
    begin = _compile(repo["prose"]["begin"])
    assert begin.match('    """')
    assert not begin.match('    """ 余計な文字')


# --------------------------------------------------------------------------
# サンプル全体 — どの行も文法のどこかに当たること
# --------------------------------------------------------------------------

def _classify(line: str, repo) -> str | None:
    if not line.strip():
        return "blank"
    for name in ("comment", "prose", "node", "attribute"):
        rule = repo[name]
        pattern = rule.get("match") or rule["begin"]
        if _compile(pattern).match(line):
            return name
    return None


def test_every_sample_line_is_covered(repo):
    src = (ROOT / "samples" / "auth-spec.pyn").read_text(encoding="utf-8")
    in_prose = False
    unmatched: list[str] = []

    for line in src.split("\n"):
        if line.strip() == '"""':
            in_prose = not in_prose
            continue
        if in_prose:
            continue  # 散文の中身は Markdown 素通しなので対象外
        if _classify(line, repo) is None:
            unmatched.append(line)

    assert unmatched == [], f"文法が拾えない行: {unmatched}"


def test_projection_b_sample_is_covered(repo):
    """投影B(フラット形)も同じ文法で読めること。"""
    from pyn import parse, to_pyn_b

    doc = parse((ROOT / "samples" / "auth-spec.pyn").read_text(encoding="utf-8"))
    in_prose = False
    unmatched: list[str] = []

    for line in to_pyn_b(doc).split("\n"):
        if line.strip() == '"""':
            in_prose = not in_prose
            continue
        if in_prose:
            continue
        if _classify(line, repo) is None:
            unmatched.append(line)

    assert unmatched == [], f"文法が拾えない行: {unmatched}"
