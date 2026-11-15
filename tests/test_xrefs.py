"""Experimental cross-file linking: scanner, workspace resolution, safe rendering."""
from __future__ import annotations

import copy
import os
import subprocess
import sys
from pathlib import Path

import pytest

from pyn import xrefs


def node(ident, prose="", title=None):
    n = {"type": "section", "id": ident, "title": title or ident}
    if prose:
        n["prose"] = [prose]
    return n


def doc(*nodes):
    return {"pyn": "1.0", "root": list(nodes)}


def test_scans_braced_cross_file_links():
    links = xrefs.scan_links("first @{another.pyn#n1}; second @{notes/b.pyn#b-2}.")
    assert [(v.path, v.node_id) for v in links] == [
        ("another.pyn", "n1"), ("notes/b.pyn", "b-2")]


def test_keeps_code_spans_uninterpreted():
    s = "`@{bad.pyn#no}` and @{good.pyn#yes}"
    assert [x.node_id for x in xrefs.scan_links(s)] == ["yes"]


def test_keeps_fenced_code_uninterpreted():
    s = "before\n```python\n@{bad.pyn#no}\n```\nafter @{good.pyn#yes}"
    assert [x.node_id for x in xrefs.scan_links(s)] == ["yes"]


def test_keeps_tilde_code_uninterpreted():
    s = "~~~\n@{bad.pyn#no}\n~~~\n@{good.pyn#yes}"
    assert len(xrefs.scan_links(s)) == 1


def test_escaped_at_symbol_is_literal():
    assert xrefs.scan_links(r"\@{foo.pyn#x}") == []


@pytest.mark.parametrize("bad", [
    "@{bad.pyn}", "@{#x}", "@{x.md#x}", "@{b.pyn#}",
    "@{/etc/passwd.pyn#x}", "@{C:/secret.pyn#x}",
    "@{a\\b.pyn#x}", "@{file.pyn#bad/id}",
    "@{file.pyn#one#two}", "@{file.pyn#x", "@{}",
])
def test_rejects_malformed_links(bad):
    with pytest.raises(xrefs.CrossReferenceError):
        xrefs.scan_links(bad)


def test_accepts_japanese_path_and_id():
    links = xrefs.scan_links("@{資料/実験.pyn#obs-01}")
    assert links[0].path == "資料/実験.pyn"


def fixture_workspace(tmp_path: Path, monkeypatch, *, broken=""):
    root = tmp_path / "root"
    root.mkdir()
    (root / "nested").mkdir()
    a = root / "a.pyn"
    b = root / "nested" / "b.pyn"
    a.write_text("stub", encoding="utf-8")
    b.write_text("stub", encoding="utf-8")
    fake = {
        a: doc(node("start", "Look @{nested/b.pyn#answer}" + broken, "Start")),
        b: doc(node("answer", "Back @{../a.pyn#start}", "Result")),
    }
    monkeypatch.setattr(xrefs, "_load_doc", lambda f: copy.deepcopy(fake[f]))
    return root, a, b, fake


def test_workspace_loads_cycle_only_once(tmp_path, monkeypatch):
    root, a, b, _ = fixture_workspace(tmp_path, monkeypatch)
    ws = xrefs.validate_workspace(a, root=root)
    assert len(ws.docs) == 2
    assert len(ws.edges) == 2
    assert ws.edges[0].target_file == b
    assert ws.edges[1].target_file == a


def test_dangling_node_is_rejected(tmp_path, monkeypatch):
    root, a, b, fake = fixture_workspace(tmp_path, monkeypatch)
    fake[b]["root"][0]["id"] = "not-answer"
    with pytest.raises(xrefs.CrossReferenceError, match="missing target"):
        xrefs.validate_workspace(a, root=root)


def test_rejects_missing_file(tmp_path, monkeypatch):
    root, a, _, fake = fixture_workspace(tmp_path, monkeypatch)
    fake[a]["root"][0]["prose"] = ["@{does-not-exist.pyn#foo}"]
    with pytest.raises((xrefs.CrossReferenceError, KeyError)):
        xrefs.validate_workspace(a, root=root)


def test_rejects_path_outside_workspace(tmp_path, monkeypatch):
    root, a, _, fake = fixture_workspace(tmp_path, monkeypatch)
    fake[a]["root"][0]["prose"] = ["@{../outside.pyn#x}"]
    with pytest.raises(xrefs.CrossReferenceError, match="outside"):
        xrefs.validate_workspace(a, root=root)


def test_symlink_escape_blocked(tmp_path, monkeypatch):
    root, a, _, fake = fixture_workspace(tmp_path, monkeypatch)
    outside = tmp_path / "other.pyn"
    outside.write_text("x")
    link = root / "link.pyn"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    fake[a]["root"][0]["prose"] = ["@{link.pyn#x}"]
    with pytest.raises(xrefs.CrossReferenceError, match="outside"):
        xrefs.validate_workspace(a, root=root)


def test_count_limit(tmp_path, monkeypatch):
    root, a, _, _ = fixture_workspace(tmp_path, monkeypatch)
    with pytest.raises(xrefs.CrossReferenceError, match="exceeds"):
        xrefs.validate_workspace(a, root=root, max_files=1)


def test_rewrite_creates_relative_links_from_actual_titles(tmp_path, monkeypatch):
    root, a, b, _ = fixture_workspace(tmp_path, monkeypatch)
    ws = xrefs.validate_workspace(a, root=root)
    targets = {a: tmp_path / "output" / "a.html",
               b: tmp_path / "output" / "nested" / "b.html"}
    original = ws.docs[a]["root"][0]["prose"][0]
    result = xrefs._rewrite(original, a, ws, targets)
    assert "[Result](nested/b.html#answer)" in result
    assert ws.docs[a]["root"][0]["prose"][0] == original


def test_render_uses_markdown_links_for_markdown_projection(tmp_path, monkeypatch):
    root, a, b, _ = fixture_workspace(tmp_path, monkeypatch)
    ws = xrefs.validate_workspace(a, root=root)
    targets = {a: tmp_path / "site" / "a.md", b: tmp_path / "site" / "nested" / "b.md"}
    result = xrefs._rewrite("@{nested/b.pyn#answer}", a, ws, targets)
    assert result == "[Result](nested/b.md#answer)"


def test_does_not_rewrite_literal_code(tmp_path, monkeypatch):
    root, a, b, _ = fixture_workspace(tmp_path, monkeypatch)
    ws = xrefs.validate_workspace(a, root=root)
    targets = {a: tmp_path / "site" / "a.html", b: tmp_path / "site" / "nested" / "b.html"}
    text = "`@{bad.pyn#thing}` and @{nested/b.pyn#answer}"
    rendered = xrefs._rewrite(text, a, ws, targets)
    assert "`@{bad.pyn#thing}`" in rendered
    assert "[Result](nested/b.html#answer)" in rendered


def test_url_encoded_and_md_safe_title(tmp_path, monkeypatch):
    root, a, b, fake = fixture_workspace(tmp_path, monkeypatch)
    fake[b]["root"][0]["title"] = "Result [review]"
    ws = xrefs.validate_workspace(a, root=root)
    targets = {a: tmp_path / "site" / "a.html", b: tmp_path / "site" / "nested" / "b.html"}
    assert xrefs._rewrite("@{nested/b.pyn#answer}", a, ws, targets) == "[Result ［review］](nested/b.html#answer)"


def test_real_integration(tmp_path):
    # Applied inside the genuine PYN repository, this is an end-to-end test.
    # The distribution-only patch workspace intentionally lacks PYN core.
    pytest.importorskip("pyn.parser")
    from pyn.refs import validate_refs
    root = Path(__file__).resolve().parents[1] / "samples" / "xrefs"
    ws = xrefs.validate_workspace(root / "index.pyn", root=root)
    assert len(ws.docs) == 3
    assert len(ws.edges) == 4
    out = tmp_path / "site"
    rendered = xrefs.build_site(ws, out, fmt="both")
    assert len(rendered) == 6
    index_html = (out / "index.html").read_text(encoding="utf-8")
    assert 'href="observations.html#obs-01"' in index_html
    assert 'href="hypotheses/theory.html#hyp-01"' in index_html
    assert 'href="#overview"' in index_html
    theory_md = (out / "hypotheses/theory.md").read_text(encoding="utf-8")
    assert "../observations.md#obs-01" in theory_md
    # Nothing changed in source documents as a side effect of rendering.
    assert "@{observations.pyn#obs-01}" in (root / "index.pyn").read_text(encoding="utf-8")


def test_real_missing_file(tmp_path):
    pytest.importorskip("pyn.parser")
    source = tmp_path / "entry.pyn"
    source.write_text('section a "A":\n  """\n  @{no.pyn#x}\n  """\n', encoding="utf-8")
    with pytest.raises(xrefs.CrossReferenceError, match="not found"):
        xrefs.validate_workspace(source)


def test_real_malformed_link(tmp_path):
    pytest.importorskip("pyn.parser")
    source = tmp_path / "entry.pyn"
    source.write_text('section a "A":\n  """\n  @{broken}\n  """\n', encoding="utf-8")
    with pytest.raises(xrefs.CrossReferenceError, match="expected"):
        xrefs.validate_workspace(source)
