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

from pyn import parse, render_md  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

#: (正本 .pyn, 生成先 .md, 文書タイトル)
DOCS = (
    ("docs/prose-dialect.pyn", "docs/prose-dialect.md",
     "散文ブロックの Markdown 方言(PYN)"),
    ("docs/agents.pyn", "AGENTS.md",
     "AGENTS.md — AI エージェント向け操作ガイド(PYN)"),
    ("docs/readme.pyn", "README.md", "PYN"),
)


def render(source: str, title: str) -> str:
    return render_md(parse((ROOT / source).read_text(encoding="utf-8")), title=title)


def main(argv=None) -> int:
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
