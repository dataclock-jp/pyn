"""正準形 → SQLite の射影(t-28・実験的)。検索の段階 2。

複数の ``.pyn`` を、ノード・属性・散文・辺の表に落とす。SQL で JOIN や再帰 CTE を書けるようにし、
FTS5(trigram)が使える環境では全文索引も張る。

    python -m pyn.db build  --in Handover.pyn docs -o pyn.sqlite     # 作る
    python -m pyn.db sql    pyn.sqlite "SELECT ..." [--for human]    # 読むだけの問い合わせ
    python -m pyn.db restore pyn.sqlite --doc Handover.pyn            # DB → .pyn(往復の確認)
    python -m pyn.db verify pyn.sqlite                                # 元の文書が変わっていないか
    python -m pyn.query find 承認 --db pyn.sqlite                     # 段階 1 の検索を DB から

**DB は正本ではなく生成物である**(d-00 / s-meta)。``sql`` は読み取り専用で開き、書き込む文は
拒否する。文書を直すのは今までどおり ``pyn patch`` で、そのあと DB を作り直す。
DB をコミットしないこと(既定の名前 ``*.sqlite`` は .gitignore にある)。

順序の列(``ord`` / ``idx``)を持たせてあるので、DB から元の正準形へ戻せる(:func:`restore`)。
往復テストがそれを保証するので、これは索引ではなく射影である(cost-2 と同じ考え方)。
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import os
import posixpath
import re
import sqlite3
import sys
import time
from pathlib import Path

from .model import canonicalize, dumps

__all__ = ["DbError", "build", "connect_ro", "restore", "verify", "run_sql",
           "fts_candidates", "SCHEMA_VERSION"]

SCHEMA_VERSION = "1"
MARKER = "pyn.db"
MAX_ROWS = 1000
TIME_BUDGET = 5.0
#: 読むだけの SQL が作れる 1 つの値の上限と、結果ぜんたいの上限(文書 1 本は 2 MB まで)
MAX_VALUE_BYTES = 8_000_000
MAX_RESULT_BYTES = 32_000_000
_INT64 = 2 ** 63 - 1

SCHEMA = """
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE docs(path TEXT PRIMARY KEY, digest TEXT NOT NULL, nodes INTEGER NOT NULL);
CREATE TABLE skipped(path TEXT PRIMARY KEY, reason TEXT NOT NULL);
CREATE TABLE nodes(
  doc TEXT NOT NULL, id TEXT NOT NULL, type TEXT NOT NULL, title TEXT,
  parent TEXT, ord INTEGER NOT NULL, depth INTEGER NOT NULL,
  PRIMARY KEY(doc, id));
CREATE INDEX nodes_type ON nodes(type);
CREATE INDEX nodes_parent ON nodes(doc, parent);
CREATE TABLE attrs(
  doc TEXT NOT NULL, node TEXT NOT NULL, key TEXT NOT NULL,
  idx INTEGER, kind TEXT NOT NULL, value,
  PRIMARY KEY(doc, node, key, idx));
CREATE INDEX attrs_key ON attrs(key, value);
CREATE TABLE prose(
  doc TEXT NOT NULL, node TEXT NOT NULL, idx INTEGER NOT NULL, text TEXT NOT NULL,
  PRIMARY KEY(doc, node, idx));
CREATE TABLE edges(
  doc TEXT NOT NULL, src TEXT NOT NULL, kind TEXT NOT NULL, via TEXT NOT NULL,
  dst_doc TEXT NOT NULL, dst TEXT NOT NULL);
CREATE INDEX edges_src ON edges(doc, src);
CREATE INDEX edges_dst ON edges(dst_doc, dst);
CREATE TABLE node_text(doc TEXT NOT NULL, node TEXT NOT NULL, text TEXT NOT NULL);
CREATE INDEX node_text_node ON node_text(doc, node);
"""

FTS_SCHEMA = """
CREATE VIRTUAL TABLE node_fts USING fts5(
  text, content='node_text', content_rowid='rowid', tokenize='trigram');
INSERT INTO node_fts(node_fts) VALUES('rebuild');
"""


class DbError(ValueError):
    """DB の指定が不正、または pyn.db の DB ではない。"""


# --------------------------------------------------------------------------
# 作る
# --------------------------------------------------------------------------

def _walk(nodes, parent=None, depth=0):
    for node in nodes:
        yield node, parent, depth
        yield from _walk(node.get("children", []), node["id"], depth + 1)


def _attr_rows(doc_path, node_id, key, value):
    """属性値を (idx, kind, value) の行へ。空の配列も 1 行で表して往復させる。"""
    def scalar(v):
        if v is None:
            return "null", None
        if isinstance(v, bool):
            return "bool", int(v)
        if isinstance(v, int):
            return "int", (v if -_INT64 - 1 <= v <= _INT64 else str(v))
        if isinstance(v, float):
            return "float", v
        return "str", v

    if isinstance(value, list):
        if not value:
            return [(doc_path, node_id, key, None, "empty_list", None)]
        return [(doc_path, node_id, key, i, *scalar(v)) for i, v in enumerate(value)]
    return [(doc_path, node_id, key, None, *scalar(value))]


def _edges(doc_path, doc):
    """辺を取り出す。どれも散文と属性からの派生値で、往復には使わない。"""
    from .xrefs import CrossReferenceError, scan_links

    ids = {n["id"] for n, _, _ in _walk(doc.get("root", []))}
    out = []
    for node, _, _ in _walk(doc.get("root", [])):
        nid = node["id"]
        for ref in node.get("refs", []):
            out.append((doc_path, nid, "ref", "prose", doc_path, ref))
        for block in node.get("prose", []):
            try:
                links = scan_links(block)
            except CrossReferenceError:
                links = []
            for link in links:
                target = posixpath.normpath(posixpath.join(posixpath.dirname(doc_path), link.path))
                if target != ".." and not target.startswith("../"):
                    out.append((doc_path, nid, "xref", "prose", target, link.node_id))
        attrs = node.get("attrs") or {}
        for key, value in attrs.items():
            for v in value if isinstance(value, list) else [value]:
                if isinstance(v, str) and v in ids:
                    out.append((doc_path, nid, key, "attr", doc_path, v))
        # pyn.knowledge の relation ノードは、それ自体が型付きの辺
        if node["type"] == "relation":
            s, k, t = attrs.get("source"), attrs.get("kind"), attrs.get("target")
            if all(isinstance(x, str) and x for x in (s, k, t)):
                out.append((doc_path, s, k, "relation", doc_path, t))
    return out


def _digest(doc: dict) -> str:
    return hashlib.sha256(dumps(doc).encode("utf-8")).hexdigest()


def _fill(conn: sqlite3.Connection, sources, skipped) -> str:
    from . import __version__
    from .query import _node_text, _norm

    conn.executescript(SCHEMA)
    for rel, doc in sources:
        doc = canonicalize(doc)
        walked = list(_walk(doc.get("root", [])))
        conn.execute("INSERT INTO docs VALUES (?, ?, ?)", (rel, _digest(doc), len(walked)))
        for ord_, (node, parent, depth) in enumerate(walked):
            nid = node["id"]
            conn.execute("INSERT INTO nodes VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (rel, nid, node["type"], node.get("title"), parent, ord_, depth))
            for key, value in (node.get("attrs") or {}).items():
                conn.executemany("INSERT INTO attrs VALUES (?, ?, ?, ?, ?, ?)",
                                 _attr_rows(rel, nid, key, value))
            conn.executemany("INSERT INTO prose VALUES (?, ?, ?, ?)",
                             [(rel, nid, i, b) for i, b in enumerate(node.get("prose", []))])
            # 照合用の文字列は pyn.query と同じ正規化(NFKC + casefold)で持つ
            conn.execute("INSERT INTO node_text VALUES (?, ?, ?)",
                         (rel, nid, _norm(_node_text(node))))
        conn.executemany("INSERT INTO edges VALUES (?, ?, ?, ?, ?, ?)", _edges(rel, doc))
    conn.executemany("INSERT INTO skipped VALUES (?, ?)", list(skipped))
    try:
        conn.executescript(FTS_SCHEMA)
        fts = "trigram"
    except sqlite3.OperationalError:
        fts = "none"  # FTS5 / trigram の無い SQLite。検索は全件走査に落ちる
    conn.executemany("INSERT INTO meta VALUES (?, ?)", [
        ("format", MARKER), ("schema_version", SCHEMA_VERSION),
        ("pyn_version", __version__), ("fts", fts)])
    conn.commit()
    return fts


def build(sources, output: str | Path, *, skipped=(), force: bool = False) -> dict:
    """文書から DB を作る。一時ファイルに書いてから置き換える(途中で失敗しても元は残る)。

    出力先にすでにファイルがあるときは、pyn.db の DB でなければ断る(``force`` で上書き)。
    """
    output = Path(output)
    if output.exists():
        if output.is_dir() or output.is_symlink():
            raise DbError(f"出力先がファイルではない: {output.name}")
        if not force and not _is_pyn_db(output):
            raise DbError(f"pyn.db の DB ではないファイルは上書きしない: {output.name}(--force で上書き)")
    tmp = output.with_name(output.name + ".tmp")
    if tmp.exists():
        tmp.unlink()
    conn = sqlite3.connect(tmp)
    try:
        fts = _fill(conn, sources, skipped)
        stats = {table: conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                 for table in ("docs", "nodes", "attrs", "prose", "edges", "skipped")}
    except BaseException:
        conn.close()
        tmp.unlink(missing_ok=True)
        raise
    conn.close()
    os.replace(tmp, output)
    stats["fts"] = fts
    return stats


def _is_pyn_db(path: Path) -> bool:
    try:
        conn = connect_ro(path, check=False)
    except (sqlite3.Error, DbError):
        return False
    try:
        row = conn.execute("SELECT value FROM meta WHERE key='format'").fetchone()
        return bool(row) and row[0] == MARKER
    except sqlite3.Error:
        return False
    finally:
        conn.close()


# --------------------------------------------------------------------------
# 読む
# --------------------------------------------------------------------------

def connect_ro(path: str | Path, *, check: bool = True) -> sqlite3.Connection:
    """読み取り専用で開く。``check`` なら pyn.db の DB であることと版を確かめる。"""
    path = Path(path)
    if not path.is_file():
        raise DbError(f"DB が見つからない: {path.name}")
    uri = path.resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.execute("PRAGMA query_only = ON")
    # DB のファイルは外から渡されうる。スキーマに仕込まれたビューやトリガーから、
    # 副作用のありうる関数や仮想表を呼ばせない。
    conn.execute("PRAGMA trusted_schema = OFF")
    if check:
        try:
            meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
        except sqlite3.Error:
            conn.close()
            raise DbError("pyn.db の DB ではない") from None
        if meta.get("format") != MARKER:
            conn.close()
            raise DbError("pyn.db の DB ではない")
        if meta.get("schema_version") != SCHEMA_VERSION:
            conn.close()
            raise DbError(f"DB の版が違う(schema_version={meta.get('schema_version')})。作り直すこと")
    return conn


def _value(kind, value):
    if kind == "null":
        return None
    if kind == "bool":
        return bool(value)
    if kind == "int":
        return int(value)
    if kind == "float":
        return float(value)
    return value


def restore(conn: sqlite3.Connection, doc: str | None = None) -> dict:
    """DB から正準形へ戻す。``{path: 正準形}``。``doc`` を渡せばその文書だけ。"""
    paths = [r[0] for r in conn.execute("SELECT path FROM docs ORDER BY rowid")]
    if doc is not None:
        if doc not in paths:
            raise DbError(f"DB に無い文書: {doc}")
        paths = [doc]
    out = {}
    for path in paths:
        nodes, order = {}, []
        for nid, typ, title, parent, _ in conn.execute(
                "SELECT id, type, title, parent, ord FROM nodes WHERE doc=? ORDER BY ord", (path,)):
            node = {"id": nid, "type": typ}
            if title is not None:
                node["title"] = title
            nodes[nid] = node
            order.append((nid, parent))
        for nid, key, idx, kind, value in conn.execute(
                "SELECT node, key, idx, kind, value FROM attrs WHERE doc=? "
                "ORDER BY node, key, idx", (path,)):
            attrs = nodes[nid].setdefault("attrs", {})
            if kind == "empty_list":
                attrs[key] = []
            elif idx is None:
                attrs[key] = _value(kind, value)
            else:
                attrs.setdefault(key, []).append(_value(kind, value))
        for nid, text in conn.execute(
                "SELECT node, text FROM prose WHERE doc=? ORDER BY node, idx", (path,)):
            nodes[nid].setdefault("prose", []).append(text)
        root = []
        for nid, parent in order:
            (root if parent is None else nodes[parent].setdefault("children", [])).append(nodes[nid])
        out[path] = canonicalize({"root": root})
    return out


def skipped_of(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    return [tuple(r) for r in conn.execute("SELECT path, reason FROM skipped ORDER BY rowid")]


def fts_candidates(conn: sqlite3.Connection, terms, match: str = "all") -> set[tuple[str, str]]:
    """検索語を含むノード ``{(path, id)}``。pyn.query の照合と同じ結果になる。

    3 文字以上の語は FTS5(trigram)の索引で絞ってから、``instr`` で部分文字列として確かめる。
    2 文字以下の語(「承認」など)は trigram の索引で引けないので、``instr`` の全件走査になる。
    """
    from .query import _norm

    has_fts = conn.execute("SELECT value FROM meta WHERE key='fts'").fetchone()[0] == "trigram"
    sets = []
    for term in terms:
        key = _norm(term)
        if has_fts and len(key) >= 3:
            phrase = '"' + key.replace('"', '""') + '"'
            rows = conn.execute(
                "SELECT doc, node FROM node_text WHERE rowid IN "
                "(SELECT rowid FROM node_fts WHERE node_fts MATCH ?) AND instr(text, ?) > 0",
                (phrase, key))
        else:
            rows = conn.execute("SELECT doc, node FROM node_text WHERE instr(text, ?) > 0", (key,))
        sets.append({tuple(r) for r in rows})
    if not sets:
        return set()
    return set.intersection(*sets) if match == "all" else set.union(*sets)


def verify(conn: sqlite3.Connection, root: str | Path) -> dict:
    """DB を作ったあとで元の文書が変わっていないか。``{"changed": [...], "missing": [...]}``。

    比べるのは正準形のダイジェストなので、`#` コメントや空白だけの変更は変更と見なさない。
    """
    from .guardrails import SecurityError, safe_read
    from .errors import PynError
    from .parser import parse

    root = Path(os.path.abspath(root))
    changed, missing = [], []
    for path, digest in conn.execute("SELECT path, digest FROM docs ORDER BY rowid"):
        parts = str(path).split("/")
        if (not str(path).endswith(".pyn")
                or any(part in ("", ".", "..") for part in parts)
                or (os.name == "nt" and ("\\" in path or ":" in path))):
            # DB に書かれたパスは信用しない。root の外を指すものは、あるかどうかも調べない
            # (調べると、任意のパスにファイルがあるかを DB 越しに確かめられてしまう)。
            missing.append(path)
            continue
        file = root.joinpath(*parts)
        if not file.is_file():
            missing.append(path)
            continue
        try:
            now = _digest(parse(safe_read(file, root=root, screen=False)))
        except (SecurityError, PynError, UnicodeError, ValueError):
            changed.append(path)
            continue
        if now != digest:
            changed.append(path)
    return {"changed": changed, "missing": missing}


# --------------------------------------------------------------------------
# 読むだけの SQL
# --------------------------------------------------------------------------

_ALLOWED = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION,
            getattr(sqlite3, "SQLITE_RECURSIVE", 33)}
_DENIED_FUNCTIONS = {"load_extension", "readfile", "writefile", "edit"}
#: 値を与えずに読むだけなら許す PRAGMA(FTS5 の MATCH が内部で使う)
_READ_PRAGMAS = {"data_version"}


def run_sql(conn: sqlite3.Connection, sql: str, *, max_rows: int = MAX_ROWS,
            time_budget: float = TIME_BUDGET):
    """読むだけの SQL を実行する。``(columns, rows, truncated)``。

    読み取り専用で開いたうえで、さらに SELECT と読み取り以外の操作(書き込み・ATTACH・PRAGMA など)を
    authorizer で拒否する。再帰 CTE の暴走は時間で打ち切る。文は 1 つだけ。
    """
    if not isinstance(sql, str) or not sql.strip():
        raise DbError("SQL が空")

    def authorizer(action, arg1, arg2, dbname, source):
        if action == sqlite3.SQLITE_FUNCTION and (arg2 or "").lower() in _DENIED_FUNCTIONS:
            return sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_PRAGMA:
            # FTS5 が内部で読む(値は与えない)。ほかの PRAGMA はすべて拒否する
            return sqlite3.SQLITE_OK if (arg1 or "").lower() in _READ_PRAGMAS and arg2 is None \
                else sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK if action in _ALLOWED else sqlite3.SQLITE_DENY

    deadline = time.monotonic() + time_budget
    conn.set_authorizer(authorizer)
    conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10_000)
    # 1 つの値の大きさと、結果ぜんたいの大きさにも上限を置く。時間の上限だけだと
    # `zeroblob(1000000000)` のように一瞬で巨大な値を作る式を止められず、メモリを使い切る。
    old_length = conn.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, MAX_VALUE_BYTES)
    try:
        cur = conn.execute(sql)
        columns = [d[0] for d in cur.description or []]
        rows, size = [], 0
        for row in cur:
            size += sum(len(v) for v in row if isinstance(v, (str, bytes)))
            if size > MAX_RESULT_BYTES:
                raise DbError(f"結果が大きすぎる({MAX_RESULT_BYTES // 1_000_000} MB まで)")
            rows.append(row)
            if len(rows) > max_rows:
                break
    except sqlite3.DatabaseError as exc:
        message = str(exc)
        if "interrupted" in message:
            raise DbError(f"時間切れ({time_budget:g} 秒)") from None
        if "too big" in message:
            raise DbError(f"値が大きすぎる({MAX_VALUE_BYTES // 1_000_000} MB まで)") from None
        if "not authorized" in message or "prohibited" in message:
            raise DbError("読み取り以外の操作は許可していない") from None
        if "one statement" in message:
            raise DbError("SQL は 1 文だけ") from None
        raise DbError(f"SQL の誤り: {message}") from None
    finally:
        conn.set_authorizer(None)
        conn.set_progress_handler(None, 0)
        conn.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, old_length)
    truncated = len(rows) > max_rows
    return columns, [list(r) for r in rows[:max_rows]], truncated


def _cell(value):
    if isinstance(value, bytes):
        return "x'" + value.hex() + "'"
    return value


#: 行の属性のうち、列ではないもの
_ROW_META = ("findings", "withheld")


def _column_keys(columns) -> list[str]:
    """列名を属性キーにする(属性キーの規則に合わない文字は _ に。重複には連番)。"""
    from .model import TYPE_RE

    keys, used = [], set(_ROW_META)
    for col in columns:
        key = re.sub(r"[^A-Za-z0-9_-]", "_", str(col)) or "col"
        if not TYPE_RE.match(key):
            key = "c_" + key
        base, n = key, 2
        while key in used:
            key, n = f"{base}_{n}", n + 1
        used.add(key)
        keys.append(key)
    return keys


def sql_result(columns, rows, truncated, *, sql: str, audience: str = "ai") -> dict:
    """SQL の結果を PYN 文書にする(``query.find`` と同じく、結果も文書として射影できる)。

    AI 向けでは、安全検査に当たったセルの値を伏せ(null)、行に ``withheld`` と ``findings`` を残す。
    """
    from .guardrails import scan_text
    from .query import AI_NOTE, AUDIENCES

    if audience not in AUDIENCES:
        raise DbError(f"audience は {' / '.join(AUDIENCES)}")
    keys = _column_keys(columns)
    row_nodes, withheld_total = [], 0
    for i, row in enumerate(rows, 1):
        attrs, withheld, codes = {}, [], set()
        for key, value in zip(keys, row):
            value = _cell(value)
            if isinstance(value, int) and not isinstance(value, bool) and abs(value) > _INT64:
                value = str(value)
            if isinstance(value, float) and not math.isfinite(value):
                value = str(value)  # 1e999 など。正準形は有限の数しか持てない
            found = scan_text(value) if isinstance(value, str) else []
            if found:
                codes.update(f.code for f in found)
                if audience == "ai":
                    withheld.append(key)
                    value = None
            attrs[key] = value
        if codes:
            attrs["findings"] = sorted(codes)
        if withheld:
            attrs["withheld"] = withheld
            withheld_total += 1
        row_nodes.append({"id": f"r{i}", "type": "row", "attrs": attrs})
    attrs = {"audience": audience, "sql": " ".join(sql.split()), "columns": list(columns),
             "rows": len(rows), "truncated": truncated}
    if withheld_total:
        attrs["withheld"] = withheld_total
    node = {"id": "sql", "type": "sql", "title": "SQL の結果", "attrs": attrs, "children": row_nodes}
    if audience == "ai":
        node["prose"] = [AI_NOTE.replace("原文を直すときは、`at` の文書と ID を宛先にして",
                                         "原文を直すときは、文書と ID を宛先にして")]
    return canonicalize({"root": [node]})


def render_sql_html(result: dict) -> str:
    from .render_html import CSS
    from .values import emit_value

    e = html.escape
    node = canonicalize(result)["root"][0]
    a = node.get("attrs", {})
    columns = a.get("columns") or []
    if isinstance(columns, str):
        columns = [columns]
    head = "".join(f"<th>{e(str(c))}</th>" for c in columns)
    keys = _column_keys(columns)  # 属性はキー順に並び替わっているので、列の順へ戻す
    body = []
    for row in node.get("children", []):
        ra = row.get("attrs", {})
        values = [ra.get(k) for k in keys]
        cells = "".join(f"<td>{e(v if isinstance(v, str) else emit_value(v))}</td>"
                        for v in values)
        warn = ""
        if ra.get("findings"):
            warn = f' class="flagged" title="安全検査に当たりました: {e(emit_value(ra["findings"]), quote=True)}"'
        body.append(f"<tr{warn}>{cells}</tr>")
    note = f"{a.get('rows', 0)} 行" + ("(上限で打ち切り)" if a.get("truncated") else "")
    return f"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SQL の結果</title>
<style>{CSS}
tr.flagged td{{background:var(--accent-soft)}}
.wrap{{max-width:72rem}}
</style>
</head>
<body>
<div class="wrap">
<header class="doc-head">
<h1>SQL の結果</h1>
<p class="sub">{e(note)}</p>
<pre><code>{e(str(a.get('sql', '')))}</code></pre>
<p class="generated">pyn.db の生成物です(DB は読み取り専用で開いています)。色の付いた行は安全検査に当たったものです。</p>
</header>
<div class="table-wrap"><table><thead><tr>{head}</tr></thead><tbody>
{chr(10).join(body)}
</tbody></table></div>
</div>
</body>
</html>
"""


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    from .cli import use_utf8_stdio, write_text
    from .query import QueryError, load_sources, MAX_FILES

    use_utf8_stdio()  # pyn の CLI と同じく UTF-8・LF で書く(t-19 / t-21)
    ap = argparse.ArgumentParser(prog="python -m pyn.db",
                                 description="正準形を SQLite へ射影する(実験的。DB は生成物)")
    sub = ap.add_subparsers(dest="action", required=True)

    b = sub.add_parser("build", help="文書から DB を作る")
    b.add_argument("--in", dest="paths", nargs="+", default=["."], metavar="PATH")
    b.add_argument("--root", help="結果のパスはここからの相対(既定: カレント)")
    b.add_argument("--max-files", type=int, default=MAX_FILES)
    b.add_argument("-o", "--output", default="pyn.sqlite")
    b.add_argument("--force", action="store_true", help="pyn.db の DB でないファイルも上書きする")

    s = sub.add_parser("sql", help="読むだけの SQL を実行する")
    s.add_argument("db")
    s.add_argument("sql")
    s.add_argument("--for", dest="audience", choices=("ai", "human"), default="ai")
    s.add_argument("--format", choices=("pyn", "json", "html"))
    s.add_argument("--max-rows", type=int, default=MAX_ROWS)
    s.add_argument("-o", "--output")

    r = sub.add_parser("restore", help="DB から文書を戻す(往復の確認)")
    r.add_argument("db")
    r.add_argument("--doc", required=True, help="root からの相対パス(DB に記録されたもの)")
    r.add_argument("--format", choices=("pyn", "json"), default="pyn")
    r.add_argument("-o", "--output")

    v = sub.add_parser("verify", help="DB を作ったあとで元の文書が変わっていないか")
    v.add_argument("db")
    v.add_argument("--root", help="文書の root(既定: カレント)")

    args = ap.parse_args(argv)
    try:
        if args.action == "build":
            sources, skipped = load_sources(args.paths, root=args.root, max_files=args.max_files)
            stats = build(sources, args.output, skipped=skipped, force=args.force)
            print("built {o}  docs={docs} nodes={nodes} attrs={attrs} prose={prose} "
                  "edges={edges} skipped={skipped} fts={fts}".format(o=args.output, **stats),
                  file=sys.stderr)
            return 0
        conn = connect_ro(args.db)
        try:
            if args.action == "sql":
                if isinstance(args.max_rows, bool) or not 1 <= args.max_rows <= 100_000:
                    raise DbError("--max-rows は 1〜100000")
                columns, rows, truncated = run_sql(conn, args.sql, max_rows=args.max_rows)
                result = sql_result(columns, rows, truncated, sql=args.sql, audience=args.audience)
                fmt = args.format or ("pyn" if args.audience == "ai" else "html")
                if fmt == "pyn":
                    from .formatter import to_pyn_a
                    text = to_pyn_a(result)
                elif fmt == "json":
                    text = dumps(result)
                else:
                    text = render_sql_html(result)
                print(f"{len(rows)} rows" + (" (truncated)" if truncated else ""), file=sys.stderr)
            elif args.action == "restore":
                doc = restore(conn, args.doc)[args.doc]
                if args.format == "pyn":
                    from .formatter import to_pyn_a
                    text = to_pyn_a(doc)
                else:
                    text = dumps(doc)
            else:
                report = verify(conn, args.root or os.getcwd())
                for path in report["changed"]:
                    print(f"changed  {path}")
                for path in report["missing"]:
                    print(f"missing  {path}")
                stale = report["changed"] or report["missing"]
                print("NG  DB が文書に追従していない — python -m pyn.db build で作り直す" if stale
                      else "OK  DB は文書に追従している", file=sys.stderr)
                return 1 if stale else 0
        finally:
            conn.close()
    except (DbError, QueryError, OSError, sqlite3.Error) as exc:
        print(f"db: {exc}", file=sys.stderr)
        return 2
    if args.output:
        write_text(Path(args.output), text)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
