"""Handover.pyn(正本)→ Handover.md / Handover.html / build/Handover.canonical.json。

t-16 で向きが反転した。それまでは `Handover.json` という **独自形** が正本で、
このモジュールは 293 行の変換器だった。JSON のキー(conclusion / rationale / …)を
PYN のノードへ写す対応表を人間が保守していたということであり、
「型と ID を持つノードの集合」という正本の定義(s-meta)から見れば、
その独自形こそが余計な中間表現だった。

いまは正本が最初から PYN 正準形なので、ここに残る仕事は射影を吐くことだけになる。
変換器は消えた。d-00 の「二重管理を作らない」を、変換規則そのものにも適用した形。

    python -m tools.handover_to_pyn          # 書き出す
    python -m tools.handover_to_pyn --check  # 差分があれば 1 を返す(CI 用)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pyn import (  # noqa: E402
    dumps,
    index_by_id,
    parse,
    render_html,
    render_md,
    validate_refs,
)

ROOT = Path(__file__).resolve().parents[1]

SOURCE = ROOT / "Handover.pyn"
TITLE = "引継ぎ — Synthesis from HTML and JSON"
NOTE_MD = ("このファイルは Handover.pyn から生成されています(手で編集しないこと)。"
           " 生成: `python -m tools.handover_to_pyn`")
NOTE_HTML = ("このページは Handover.pyn から生成されています(手で編集しないこと)。"
             " 生成: python -m tools.handover_to_pyn")


def load() -> dict:
    doc = parse(SOURCE.read_text(encoding="utf-8"))
    validate_refs(doc)
    return doc


def subtitle(doc: dict) -> str:
    """meta ノードの属性から副題を作る。正本の中の値を使い、二重に書かない。"""
    attrs = index_by_id(doc).get("meta", {}).get("attrs", {})
    return f"{attrs.get('project', '')} / 最終更新 {attrs.get('last_session', '')}"


def outputs(doc: dict) -> dict[Path, str]:
    return {
        ROOT / "Handover.md": render_md(
            doc, title=TITLE, subtitle=subtitle(doc), note=NOTE_MD),
        ROOT / "Handover.html": render_html(
            doc, title=TITLE, subtitle=subtitle(doc), note=NOTE_HTML),
        ROOT / "build" / "Handover.canonical.json": dumps(doc),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="handover_to_pyn", description=__doc__)
    ap.add_argument("--check", action="store_true",
                    help="書き出さず、追従しているかだけを見る")
    args = ap.parse_args(argv)

    doc = load()
    produced = outputs(doc)

    if args.check:
        stale = [p.name for p, text in produced.items()
                 if not p.exists() or p.read_text(encoding="utf-8") != text]
        if stale:
            print("NG  Handover.pyn に追従していません: " + ", ".join(stale)
                  + " — python -m tools.handover_to_pyn で再生成", file=sys.stderr)
            return 1
        print("OK  生成物は追従しています")
        return 0

    for path, text in produced.items():
        path.parent.mkdir(exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")

    print(f"OK  nodes={sum(1 for _ in index_by_id(doc))}")
    for path in produced:
        print(f"  {path.relative_to(ROOT).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
