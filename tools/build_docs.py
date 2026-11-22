""".pyn で書いた文書から Markdown 射影を生成する。

正本は `.pyn` 側。`.md` は生成物なので手で編集しない(d-00)。
どの `.pyn` からどの `.md` を、どんなタイトルで作るかはここが唯一の定義であり、
tests/test_docs.py が追従を強制する。

    python -m tools.build_docs          # 書き出す
    python -m tools.build_docs --check  # 差分があれば 1 を返す(CI 用)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pyn.cli import use_utf8_stdio  # noqa: E402
from pyn import parse, render_md  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

#: (正本 .pyn, 生成先 .md, 文書タイトル)
DOCS = (
    ("docs/prose-dialect.pyn", "docs/prose-dialect.md",
     "散文ブロックの Markdown 方言(PYN)"),
    ("docs/agents.pyn", "AGENTS.md",
     "AGENTS.md — AI エージェント向け操作ガイド(PYN)"),
    ("docs/readme.pyn", "README.md", "PYN"),
    ("docs/knowledge-memory.pyn", "docs/knowledge-memory.md",
     "型付きの知識関係(PYN 拡張・実験的)"),
    ("docs/cross-file-links.pyn", "docs/cross-file-links.md",
     "文書間リンク(PYN 拡張・実験的)"),
    ("docs/impact-security.pyn", "docs/impact-security.md",
     "変更の影響分析と取り込み前の安全検査(PYN 拡張・実験的)"),
    ("docs/knowledge-intake.pyn", "docs/knowledge-intake.md",
     "知識の取り込み審査(PYN 拡張・実験的)"),
    ("docs/query.pyn", "docs/query.md",
     "複数文書の検索(PYN 拡張・実験的)"),
    ("docs/db.pyn", "docs/db.md",
     "SQLite への射影(PYN 拡張・実験的)"),
)


def note(source: str) -> str:
    """生成物の先頭に出す注記。Handover.md と同じ形(tools/handover_to_pyn の NOTE_MD)。

    以前は正本の `.pyn` の 1 行目に `#` コメントで書いていたが、コメントは
    正準形に保存されないので patch のたびに消え、手で戻していた(d-02)。
    「手で編集しない」を読む必要があるのは生成物を開いた人なので、こちらに出す。
    """
    return (f"このファイルは {source} から生成されています(手で編集しないこと)。"
            " 生成: `python -m tools.build_docs`")


def render(source: str, title: str) -> str:
    return render_md(parse((ROOT / source).read_text(encoding="utf-8")),
                     title=title, note=note(source))


def main(argv=None) -> int:
    use_utf8_stdio()  # CLI と同じく UTF-8・LF で書く(t-19 / t-21)
    ap = argparse.ArgumentParser(prog="build_docs", description=__doc__)
    ap.add_argument("--check", action="store_true",
                    help="書き出さず、追従しているかだけを見る")
    args = ap.parse_args(argv)

    stale: list[str] = []
    for source, target, title in DOCS:
        expected = render(source, title)
        path = ROOT / target
        current = path.read_text(encoding="utf-8") if path.exists() else None

        if args.check:
            if current != expected:
                stale.append(target)
            continue

        path.write_text(expected, encoding="utf-8", newline="\n")
        print(f"wrote {target}  ({source} から)")

    if args.check:
        if stale:
            print("NG  正本に追従していません: " + ", ".join(stale)
                  + " — python -m tools.build_docs で再生成", file=sys.stderr)
            return 1
        print("OK  生成物は追従しています")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
