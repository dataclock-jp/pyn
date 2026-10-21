"""PYN の CLI。射影を切り替えるだけの薄い層。

    python -m pyn json  samples/auth-spec.pyn     # 正準形 JSON へ
    python -m pyn a     samples/auth-spec.pyn     # 投影A(インデント形)へ
    python -m pyn b     samples/auth-spec.pyn     # 投影B(フラット形)へ
    python -m pyn html  samples/auth-spec.pyn     # HTML へ
    python -m pyn md    samples/auth-spec.pyn     # Markdown へ
    python -m pyn check  samples/auth-spec.pyn    # 検証のみ(往復 + 参照 + schema)
    python -m pyn repair broken.pyn               # インデント破損の診断と修復候補
    python -m pyn patch  doc.pyn --ops ops.json   # ID 宛のパッチを当てて射影を再生成
    python -m pyn patch  doc.pyn --ops ops.json --dry-run  # 当ててみるだけ(書かない)
    python -m pyn id     draft.pyn                # ID の無い宣言に採番する(cost-1)

入力は .pyn / .json のどちらでもよい(正本は同じものなので)。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .errors import PynError
from .formatter import to_pyn_a, to_pyn_b
from .model import canonicalize, dumps
from .autoid import assign_ids
from .parser import comment_lines, detect_projection, parse
from .patch import apply as apply_patch
from .refs import iter_nodes, validate_refs
from .render_html import render_html
from .render_md import render_md
from .repair import diagnose, report
from .schema import load_schema, validate_schema


def load(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".json":
        return canonicalize(json.loads(text))
    return parse(text)


def write_text(path: Path, text: str) -> None:
    """ファイルへ書く。UTF-8、改行は常に LF(t-21)。

    `Path.write_text` の既定はテキストモードなので、Windows では改行が CRLF に
    変わる。.pyn も生成物も LF を前提にしており(リポジトリの .gitattributes も
    eol=lf)、OS によって書き出すバイト列が変わると差分や比較が狂う。
    """
    path.write_text(text, encoding="utf-8", newline="\n")


def use_utf8_stdio() -> None:
    """標準出力・標準エラーを UTF-8・LF に固定する(-o のファイル出力と同じ)。

    Windows ではパイプやリダイレクト先がロケール既定(cp932)になり、`⇄` や `—` の
    ような cp932 に無い文字で UnicodeEncodeError になる。エージェントは出力を必ず
    パイプで受けるので、これは「たまに化ける」ではなく「確実に落ちる」経路だった。
    `pyn a doc.pyn > out.pyn` の結果が UTF-8 でないと、そのまま読み戻せないことにもなる。
    改行も変換しない(t-21)。リダイレクトした結果が -o と同じバイト列になるように。
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", newline="\n")


#: 警告に並べるコメント行の最大数(それ以上は件数だけ出す)
COMMENT_PREVIEW = 3


def warn_dropped_comments(path: Path, destination: Path | None) -> None:
    """コメントが失われることを、失う前に知らせる(t-14)。

    ``#`` は表層構文であって正準形には保存されない。射影を再生成すれば必ず消える。
    消えること自体は s-meta からの当然の帰結なので直さないが、
    **黙って消すのはやめる**。特に patch は既定で入力を上書きする。
    """
    if path.suffix == ".json":
        return
    found = comment_lines(path.read_text(encoding="utf-8"))
    if not found:
        return

    where = "入力を上書きするので失われます" if destination == path \
        else "出力には含まれません"
    print(f"警告: # コメント {len(found)} 行は正準形に保存されないため、{where}"
          f"(d-02 / t-14)", file=sys.stderr)
    for line_no, raw in found[:COMMENT_PREVIEW]:
        print(f"  line {line_no}: {raw.strip()}", file=sys.stderr)
    if len(found) > COMMENT_PREVIEW:
        print(f"  ... 他 {len(found) - COMMENT_PREVIEW} 行", file=sys.stderr)


#: 各アクションが実際に読むフラグ。ここに無いフラグを渡されたら黙って無視せず断る。
#:
#: 無視は t-14 と同じ罪である。特に patch は既定で入力を上書きするので、効かない
#: `--dry-run` を黙認すると「試したつもり」が上書きそのものになる(実際に起きた)。
#: 使えないフラグは、書き換えの前に、その場で言う。
ACCEPTED_FLAGS = {
    "json": {"out", "no_refs"},
    "a": {"out", "no_refs"},
    "b": {"out", "no_refs"},
    "html": {"out", "title", "no_refs"},
    "md": {"out", "title", "no_refs"},
    "check": {"no_refs"},
    "repair": set(),
    "patch": {"out", "no_refs", "dry_run", "ops", "as_projection"},
    "id": {"out", "dry_run"},
}

#: 属性名 → コマンドラインでの綴り(エラーメッセージを利用者の語彙で書くため)
FLAG_SPELLING = {
    "out": "-o/--out",
    "title": "--title",
    "no_refs": "--no-refs",
    "dry_run": "--dry-run",
    "ops": "--ops",
    "as_projection": "--as",
}


def unusable_flags(args) -> list[str]:
    """指定されたが、そのアクションでは読まれないフラグの綴りを返す。"""
    accepted = ACCEPTED_FLAGS[args.action]
    return [spelling for name, spelling in FLAG_SPELLING.items()
            if name not in accepted and getattr(args, name) not in (None, False)]


def main(argv=None) -> int:
    use_utf8_stdio()
    ap = argparse.ArgumentParser(prog="pyn", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action",
                    choices=["json", "a", "b", "html", "md", "check", "repair", "patch", "id"])
    ap.add_argument("path", type=Path)
    ap.add_argument("-o", "--out", type=Path, help="出力先(既定は標準出力)")
    ap.add_argument("--title", default=None, help="html / md のタイトル")
    ap.add_argument("--no-refs", action="store_true",
                    help="未定義参照のチェックを行わない")
    ap.add_argument("--dry-run", action="store_true",
                    help="patch / id: 書き換えず、結果だけを出す")
    ap.add_argument("--ops", type=Path,
                    help="patch: 操作リスト(JSON配列)のファイル。- で標準入力")
    ap.add_argument("--as", dest="as_projection", choices=["json", "a", "b"],
                    help="patch: 出力の射影(既定は入力と同じ)")
    args = ap.parse_args(argv)

    unusable = unusable_flags(args)
    if unusable:
        print(f"{args.action} は {', '.join(unusable)} を読みません"
              f" — 効かないフラグを黙って無視しないためにここで止めます",
              file=sys.stderr)
        return 2

    if args.action == "id":
        # 採番はテキストのまま行う(コメントも並びも保つ)。壊れた入力は strict な
        # パースで弾かれるので、ここでは読み込みを通さない。
        source = args.path.read_text(encoding="utf-8")
        try:
            assigned_text, assigned = assign_ids(source)
            parse(assigned_text)
        except PynError as exc:
            print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
            return 1

        if not assigned:
            print("採番するものはありません(すべての宣言に ID があります)",
                  file=sys.stderr)
            return 0
        for line_no, new_id in assigned:
            print(f"  line {line_no}: {new_id}", file=sys.stderr)
        if args.dry_run:
            print(f"{len(assigned)} 件(--dry-run のため書き換えていません)",
                  file=sys.stderr)
            return 0
        target = args.out or args.path
        write_text(target, assigned_text)
        print(f"assigned {len(assigned)} ids -> {target}", file=sys.stderr)
        return 0

    if args.action == "repair":
        # 破損している可能性があるのでパースを通さずに診断する。
        # cost-3 により自動適用はしない(候補の提示のみ)。
        findings = diagnose(args.path.read_text(encoding="utf-8"))
        print(report(findings))
        return 1 if findings else 0

    try:
        doc = load(args.path)

        if args.action == "patch":
            if args.ops is None:
                print("patch には --ops が必要です", file=sys.stderr)
                return 2
            raw = sys.stdin.read() if str(args.ops) == "-" \
                else args.ops.read_text(encoding="utf-8")
            ops = json.loads(raw)
            doc = apply_patch(doc, ops)
            # パッチ後にリンク切れが出ていないかを必ず見る(s-02)
            if not args.no_refs:
                validate_refs(doc)
            projection = args.as_projection or (
                "json" if args.path.suffix == ".json" else
                (detect_projection(args.path.read_text(encoding="utf-8"))
                 or "A").lower()
            )
            out = {"json": dumps, "a": to_pyn_a, "b": to_pyn_b}[projection](doc)
            if args.out is None:
                args.out = args.path  # パッチは既定で上書き(射影は再生成物なので)
            if args.dry_run:
                # 当ててみて、参照まで通ることを確かめて、何も書かずに終わる。
                # 結果を見たいだけなら -o で別ファイルへ出せばよい(原本は触らない)ので、
                # ここで標準出力へ流すことはしない。id --dry-run と同じ振る舞い。
                print(f"{len(ops)} 件の操作を適用できます"
                      f"(--dry-run のため {args.out} は書き換えていません)",
                      file=sys.stderr)
                return 0
            warn_dropped_comments(args.path, args.out)
            write_text(args.out, out)
            print(f"patched {args.out}  (projection={projection})", file=sys.stderr)
            return 0

        if not args.no_refs:
            validate_refs(doc)

        if args.action == "check":
            for projection, fmt in (("A", to_pyn_a), ("B", to_pyn_b)):
                if parse(fmt(doc)) != doc:
                    print(f"NG: 投影{projection} の往復が一致しません", file=sys.stderr)
                    return 1
            # t-10: 実装(canonicalize)だけでなく、配布している schema にも通す(t-20: pip で
            # 入れた環境でも同じ schema になるよう、ファイルではなく build_schema() から作る)
            validate_schema(doc, load_schema())
            n = sum(1 for _ in iter_nodes(doc))
            src = detect_projection(args.path.read_text(encoding="utf-8")) \
                if args.path.suffix != ".json" else "-"
            print(f"OK  nodes={n}  source_projection={src}  往復A/B一致  参照解決OK  schema適合")
            return 0

        if args.action in ("json", "a", "b"):
            warn_dropped_comments(args.path, args.out)

        if args.action == "json":
            out = dumps(doc)
        elif args.action == "a":
            out = to_pyn_a(doc)
        elif args.action == "b":
            out = to_pyn_b(doc)
        elif args.action == "md":
            out = render_md(doc, title=args.title or args.path.stem)
        else:
            out = render_html(doc, title=args.title or args.path.stem)

    except PynError as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    if args.out:
        write_text(args.out, out)
        print(f"wrote {args.out}", file=sys.stderr)
    else:
        sys.stdout.write(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
