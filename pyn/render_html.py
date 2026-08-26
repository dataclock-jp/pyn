"""正準形 JSON → HTML(t-06)。

d-00 の原則:
    HTML は *生成物* である。手で編集してはならない。
s-02 の実装:
    散文中の @id はここで実際のリンクへ解決される。
    参照先のタイトルを自動補完するので、リンクテキストを二重管理しなくてよい。

外部依存なしの自己完結 HTML を出力する(CSS はインライン、フォントはシステム)。
散文の Markdown は必要十分な部分集合のみを解釈する(q-03 の決定に従い見出しは非対応)。
"""

from __future__ import annotations

import html
import re

from .model import canonicalize
from .refs import index_by_id
from .values import emit_value

# --------------------------------------------------------------------------
# インライン Markdown
# --------------------------------------------------------------------------

CODE_SPAN = re.compile(r"`([^`\n]+)`")
LINK = re.compile(r"\[([^\]\n]+)\]\(([^)\s]+)\)")
BOLD = re.compile(r"\*\*([^*\n]+)\*\*")
ITALIC = re.compile(r"(?<!\*)\*([^*\n]+)\*(?!\*)")
REF = re.compile(r"(?<![A-Za-z0-9_])([@§])([A-Za-z0-9_][A-Za-z0-9_.-]*)")

_SENTINEL = "\x00CODE{}\x00"


def _inline(text: str, index: dict) -> str:
    stash: list[str] = []

    def _keep(m):
        stash.append(f"<code>{html.escape(m.group(1))}</code>")
        return _SENTINEL.format(len(stash) - 1)

    text = CODE_SPAN.sub(_keep, text)
    text = html.escape(text, quote=False)

    def _ref(m):
        sigil, target = m.group(1), m.group(2)
        node = index.get(target)
        if node is None:
            return (f'<span class="ref ref--dangling" title="未定義のID">'
                    f"{sigil}{target}</span>")
        label = node.get("title") or target
        return (f'<a class="ref" href="#{target}">'
                f"{html.escape(label)}</a>")

    text = REF.sub(_ref, text)
    text = LINK.sub(
        lambda m: f'<a href="{html.escape(m.group(2), quote=True)}">{m.group(1)}</a>',
        text)
    text = BOLD.sub(lambda m: f"<strong>{m.group(1)}</strong>", text)
    text = ITALIC.sub(lambda m: f"<em>{m.group(1)}</em>", text)

    for i, chunk in enumerate(stash):
        text = text.replace(_SENTINEL.format(i), chunk)
    return text


# --------------------------------------------------------------------------
# ブロック Markdown(部分集合)
# --------------------------------------------------------------------------

FENCE = re.compile(r"^\s*(```|~~~)\s*(\S*)\s*$")
UL = re.compile(r"^\s*[-*+]\s+(.*)$")
OL = re.compile(r"^\s*\d+\.\s+(.*)$")
QUOTE = re.compile(r"^\s*>\s?(.*)$")
TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")


def _markdown(text: str, index: dict) -> str:
    lines = text.split("\n")
    out: list[str] = []
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i]

        if not line.strip():
            i += 1
            continue

        m = FENCE.match(line)
        if m:
            lang = m.group(2)
            body: list[str] = []
            i += 1
            while i < n and not FENCE.match(lines[i]):
                body.append(lines[i])
                i += 1
            i += 1
            cls = f' class="lang-{html.escape(lang)}"' if lang else ""
            out.append(f"<pre><code{cls}>"
                       + html.escape("\n".join(body)) + "</code></pre>")
            continue

        if "|" in line and i + 1 < n and TABLE_SEP.match(lines[i + 1]):
            head = _table_cells(line)
            i += 2
            rows = []
            while i < n and "|" in lines[i] and lines[i].strip():
                rows.append(_table_cells(lines[i]))
                i += 1
            out.append(_table_html(head, rows, index))
            continue

        if UL.match(line) or OL.match(line):
            ordered = bool(OL.match(line))
            pattern = OL if ordered else UL
            items = []
            while i < n and pattern.match(lines[i]):
                items.append(pattern.match(lines[i]).group(1))
                i += 1
            tag = "ol" if ordered else "ul"
            body = "".join(f"<li>{_inline(x, index)}</li>" for x in items)
            out.append(f"<{tag}>{body}</{tag}>")
            continue

        if QUOTE.match(line):
            items = []
            while i < n and QUOTE.match(lines[i]):
                items.append(QUOTE.match(lines[i]).group(1))
                i += 1
            out.append("<blockquote>"
                       + _markdown("\n".join(items), index)
                       + "</blockquote>")
            continue

        para = []
        while i < n and lines[i].strip() and not (
            FENCE.match(lines[i]) or UL.match(lines[i])
            or OL.match(lines[i]) or QUOTE.match(lines[i])
        ):
            para.append(lines[i])
            i += 1
        out.append("<p>" + _inline("\n".join(para), index).replace("\n", "<br>")
                   + "</p>")

    return "\n".join(out)


def _table_cells(line: str) -> list[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def _table_html(head, rows, index) -> str:
    th = "".join(f"<th>{_inline(c, index)}</th>" for c in head)
    body = "".join(
        "<tr>" + "".join(f"<td>{_inline(c, index)}</td>" for c in r) + "</tr>"
        for r in rows
    )
    return (f'<div class="table-wrap"><table><thead><tr>{th}</tr></thead>'
            f"<tbody>{body}</tbody></table></div>")


# --------------------------------------------------------------------------
# ノード
# --------------------------------------------------------------------------

def _node_html(node: dict, index: dict, level: int) -> str:
    parts: list[str] = []
    depth = min(level, 4)
    parts.append(f'<section class="node node--{html.escape(node["type"])} '
                 f'depth-{depth}" id="{html.escape(node["id"], quote=True)}">')

    title = node.get("title")
    parts.append('<header class="node__head">')
    parts.append(f'<span class="badge">{html.escape(node["type"])}</span>')
    if title:
        parts.append(f'<h2 class="node__title">{html.escape(title)}</h2>')
    parts.append(f'<a class="node__id" href="#{html.escape(node["id"], quote=True)}">'
                 f'{html.escape(node["id"])}</a>')
    parts.append("</header>")

    attrs = node.get("attrs") or {}
    if attrs:
        rows = "".join(
            f"<tr><th>{html.escape(k)}</th>"
            f'<td><code>{html.escape(emit_value(v))}</code></td></tr>'
            for k, v in attrs.items()
        )
        parts.append(f'<div class="table-wrap"><table class="attrs">'
                     f"<tbody>{rows}</tbody></table></div>")

    for block in node.get("prose", []):
        parts.append(f'<div class="prose">{_markdown(block, index)}</div>')

    refs = node.get("refs") or []
    if refs:
        chips = "".join(
            f'<a class="chip" href="#{html.escape(r, quote=True)}">'
            f'{html.escape((index[r].get("title") or r) if r in index else r)}</a>'
            for r in refs
        )
        parts.append(f'<div class="edges"><span class="edges__label">→ 参照</span>'
                     f"{chips}</div>")

    for child in node.get("children", []):
        parts.append(_node_html(child, index, level + 1))

    parts.append("</section>")
    return "\n".join(parts)


# --------------------------------------------------------------------------

CSS = """
:root{
  --bg:#fbfbfa; --surface:#ffffff; --line:#e4e2dd; --line-soft:#efedea;
  --fg:#1f1e1c; --fg-dim:#6b6862; --accent:#8a5a2b; --accent-soft:#f2ece4;
  --badge-bg:#efedea; --code-bg:#f5f3f0; --warn:#a33;
}
:root:not([data-theme="light"]){ }
@media (prefers-color-scheme: dark){
  :root:not([data-theme="light"]){
    --bg:#191817; --surface:#211f1e; --line:#35322f; --line-soft:#2b2927;
    --fg:#eceae6; --fg-dim:#9a958d; --accent:#d9a066; --accent-soft:#2e2721;
    --badge-bg:#2b2927; --code-bg:#262422; --warn:#e08a8a;
  }
}
:root[data-theme="dark"]{
  --bg:#191817; --surface:#211f1e; --line:#35322f; --line-soft:#2b2927;
  --fg:#eceae6; --fg-dim:#9a958d; --accent:#d9a066; --accent-soft:#2e2721;
  --badge-bg:#2b2927; --code-bg:#262422; --warn:#e08a8a;
}
*{box-sizing:border-box}
body{
  margin:0; background:var(--bg); color:var(--fg);
  font-family:"Hiragino Kaku Gothic ProN","Yu Gothic UI","Meiryo",
              system-ui,-apple-system,"Segoe UI",sans-serif;
  line-height:1.75; font-size:16px;
}
.wrap{max-width:52rem; margin:0 auto; padding:3rem 1.25rem 6rem}
.doc-head{border-bottom:1px solid var(--line); padding-bottom:1.5rem; margin-bottom:2rem}
.doc-head h1{font-size:1.65rem; line-height:1.35; margin:0 0 .4rem}
.doc-head .sub{color:var(--fg-dim); font-size:.9rem; margin:0}
.generated{
  margin-top:1rem; font-size:.78rem; color:var(--fg-dim);
  border-left:2px solid var(--accent); padding-left:.7rem;
}
.node{
  background:var(--surface); border:1px solid var(--line);
  border-radius:10px; padding:1.1rem 1.2rem; margin:0 0 1rem;
}
.node .node{margin-top:1rem; margin-bottom:0; background:transparent;
  border-style:dashed; border-color:var(--line-soft)}
.node__head{display:flex; align-items:baseline; gap:.6rem; flex-wrap:wrap;
  margin-bottom:.6rem}
.node__title{font-size:1.05rem; margin:0; font-weight:650; flex:1 1 auto}
.badge{
  font-size:.68rem; letter-spacing:.06em; text-transform:uppercase;
  background:var(--badge-bg); color:var(--fg-dim);
  padding:.15rem .5rem; border-radius:4px; white-space:nowrap;
}
.node__id{font-size:.72rem; color:var(--fg-dim); text-decoration:none;
  font-family:ui-monospace,"SFMono-Regular",Consolas,monospace}
.node__id:hover{color:var(--accent)}
.prose{margin:.5rem 0}
.prose p{margin:.6rem 0}
.prose ul,.prose ol{margin:.6rem 0; padding-left:1.4rem}
.prose li{margin:.2rem 0}
blockquote{margin:.7rem 0; padding:.1rem 0 .1rem .9rem;
  border-left:3px solid var(--line); color:var(--fg-dim)}
code{background:var(--code-bg); padding:.1rem .35rem; border-radius:4px;
  font-family:ui-monospace,"SFMono-Regular",Consolas,monospace; font-size:.86em}
pre{background:var(--code-bg); padding:.9rem 1rem; border-radius:8px;
  overflow-x:auto; border:1px solid var(--line-soft)}
pre code{background:none; padding:0; font-size:.82rem; line-height:1.6}
.table-wrap{overflow-x:auto; margin:.7rem 0}
table{border-collapse:collapse; width:100%; font-size:.88rem}
th,td{border:1px solid var(--line); padding:.4rem .6rem; text-align:left;
  vertical-align:top}
th{background:var(--badge-bg); font-weight:600}
table.attrs th{width:9rem; white-space:nowrap; color:var(--fg-dim);
  font-weight:500; font-size:.82rem}
a{color:var(--accent)}
.ref{
  text-decoration:none; border-bottom:1px solid var(--accent);
  padding-bottom:1px;
}
.ref--dangling{color:var(--warn); border-bottom:1px dotted var(--warn)}
.edges{display:flex; gap:.4rem; flex-wrap:wrap; align-items:center;
  margin-top:.8rem; padding-top:.7rem; border-top:1px dashed var(--line-soft)}
.edges__label{font-size:.72rem; color:var(--fg-dim); white-space:nowrap}
.chip{
  font-size:.75rem; background:var(--accent-soft); color:var(--accent);
  padding:.15rem .55rem; border-radius:99px; text-decoration:none;
  border:1px solid transparent;
}
.chip:hover{border-color:var(--accent)}
:target{scroll-margin-top:1.5rem}
:target > .node__head .node__title{color:var(--accent)}
"""


def render_html(doc: dict, *, title: str = "PYN document",
                subtitle: str | None = None,
                note: str | None = None) -> str:
    """正準形から自己完結した HTML 文書を生成する。"""
    doc = canonicalize(doc)
    index = index_by_id(doc)
    body = "\n".join(_node_html(n, index, 0) for n in doc["root"])

    head_extra = ""
    if subtitle:
        head_extra += f'<p class="sub">{html.escape(subtitle)}</p>'
    if note:
        head_extra += f'<p class="generated">{html.escape(note)}</p>'

    return f"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>{CSS}</style>
</head>
<body>
<div class="wrap">
<header class="doc-head">
<h1>{html.escape(title)}</h1>
{head_extra}
</header>
{body}
</div>
</body>
</html>
"""
