"""schema/canonical.schema.json を pyn.schema.build_schema() から生成する(t-10)。

正準形の定義は pyn/model.py にしかない。schema はその射影(生成物)である。
手で編集しないこと。

    python -m tools.build_schema          # 書き出す
    python -m tools.build_schema --check  # 差分があれば 1 を返す(CI 用)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pyn.schema import SCHEMA_PATH, dumps_schema  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="build_schema", description=__doc__)
    ap.add_argument("--check", action="store_true",
                    help="書き出さず、追従しているかだけを見る")
    args = ap.parse_args(argv)

    expected = dumps_schema()
    current = SCHEMA_PATH.read_text(encoding="utf-8") if SCHEMA_PATH.exists() else None

    if args.check:
        if current == expected:
            print(f"OK  {SCHEMA_PATH.name} は追従しています")
            return 0
        print(f"NG  {SCHEMA_PATH.name} が古い — python -m tools.build_schema で再生成",
              file=sys.stderr)
        return 1

    SCHEMA_PATH.parent.mkdir(exist_ok=True)
    SCHEMA_PATH.write_text(expected, encoding="utf-8", newline="\n")
    print(f"wrote {SCHEMA_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
