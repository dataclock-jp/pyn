"""t-17: ID が黙って消えていないかを見張る。

cost-1 の本体は「ID を振ること」ではなく **「振った ID を変えないこと」** である。
ID は時間をまたいで安定しているからこそ、`@参照` の宛先にも、パッチの宛先にも、
過去の議論の指し先にもなれる。振り直した瞬間にその全部が壊れる。

採番(`python -m pyn id`)は前者を自動化するが、後者は自動化できない。
できるのは「消えたことに気づく」ことだけなので、それをやる。

    python -m tools.check_ids                 # 作業ツリー vs HEAD
    python -m tools.check_ids --base HEAD~1   # 直前のコミット vs いま(CI 用)

意図的に廃止した ID は `retired-ids.txt` に書けば通る。
面倒に見えるが、それが狙いである —— ID の廃止は記録に値する出来事で、
黙って消えるのが一番困る。
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pyn import index_by_id, parse  # noqa: E402
from pyn.errors import PynError  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
RETIRED = ROOT / "retired-ids.txt"


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True,
                          text=True, encoding="utf-8").stdout


def tracked_pyn_files() -> list[str]:
    return [line for line in git("ls-files", "*.pyn").split("\n") if line.strip()]


def ids_of(text: str, where: str) -> set[str]:
    try:
        return set(index_by_id(parse(text)))
    except PynError as exc:
        raise SystemExit(f"{where}: 解析できません — {exc}")


def retired_ids() -> set[str]:
    if not RETIRED.exists():
        return set()
    out = set()
    for line in RETIRED.read_text(encoding="utf-8").split("\n"):
        line = line.split("#", 1)[0].strip()
        if line:
            out.add(line)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="check_ids", description=__doc__)
    ap.add_argument("--base", default="HEAD",
                    help="比較元のリビジョン(既定: HEAD = 作業ツリーと比べる)")
    ap.add_argument("paths", nargs="*", help="対象(既定: 追跡下の全 .pyn)")
    args = ap.parse_args(argv)

    if not git("rev-parse", "--verify", args.base).strip():
        print(f"{args.base} が無いので比較しません(初回コミットなど)")
        return 0

    allowed = retired_ids()
    paths = args.paths or tracked_pyn_files()
    problems: list[str] = []

    for path in paths:
        before = git("show", f"{args.base}:{path}")
        if not before:
            continue  # 比較元に無い = 新規ファイル
        current = ROOT / path
        if not current.exists():
            print(f"警告: {path} が削除されています(ID の追跡対象から外れます)")
            continue

        old = ids_of(before, f"{args.base}:{path}")
        new = ids_of(current.read_text(encoding="utf-8"), path)

        gone = sorted(old - new - allowed)
        if gone:
            added = sorted(new - old)
            problems.append(
                f"{path}: 消えた ID {len(gone)} 件 — " + ", ".join(gone)
                + (f"\n    (増えた ID: {', '.join(added)})" if added else "")
            )

    if problems:
        print("NG  ID が消えています(cost-1: 振った ID は変えない)", file=sys.stderr)
        for line in problems:
            print("  " + line, file=sys.stderr)
        print(
            "\n  直し方は 2 つのどちらか:\n"
            "    1. ID を元に戻す(改名なら pyn patch の rename を使う。"
            "参照も一緒に張り替わる)\n"
            f"    2. 意図的な廃止なら {RETIRED.name} に ID と理由を1行で書く",
            file=sys.stderr)
        return 1

    print(f"OK  ID は保たれています({len(paths)} ファイル / 基準 {args.base})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
