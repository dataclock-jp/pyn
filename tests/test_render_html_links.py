"""散文の Markdown リンクを HTML にするとき、危険な URL スキームを通さないことの検証(t-25)。

`[文字](javascript:...)` がそのまま `<a href="javascript:...">` になっていた。
PYN は AI が書いた文書や外から来た文書を HTML にして人が開く使い方をするので、
クリック一つでスクリプトが動くリンクを生成物に出してはいけない。
(ChatGPT がこのリポジトリを読んで最初に指摘した点。)
"""

from __future__ import annotations

import pytest

from pyn import parse, render_html


def _html(link: str) -> str:
    return render_html(parse(f'spec s "t":\n  """\n  前 {link} 後\n  """\n'))


@pytest.mark.parametrize("url", [
    "javascript:alert(1)",
    "JavaScript:alert(1)",
    "vbscript:msgbox",
    "data:text/html;base64,PHNjcmlwdD4=",
    "file:///etc/passwd",
])
def test_dangerous_schemes_are_not_linked(url):
    out = _html(f"[押す]({url})")
    scheme = url.split(":")[0].lower()
    assert f'href="{scheme}:' not in out.lower()
    assert 'class="link--blocked"' in out
    assert "押す" in out


@pytest.mark.parametrize("url", [
    "https://example.com/a?x=1&y=2",
    "http://example.com/",
    "mailto:someone@example.com",
    "docs/prose-dialect.md#prose-dialect",
    "#top",
    "../README.md",
])
def test_safe_urls_stay_links(url):
    out = _html(f"[行く]({url})")
    assert f'<a href="{url.replace("&", "&amp;")}">行く</a>' in out


def test_ampersand_is_escaped_once():
    """以前は本文を一度エスケープしてから href を再びエスケープしており、& が &amp;amp; になっていた。"""
    out = _html("[q](https://example.com/?a=1&b=2)")
    assert "&amp;amp;" not in out
    assert 'href="https://example.com/?a=1&amp;b=2"' in out
