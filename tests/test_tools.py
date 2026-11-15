"""リポジトリ保守用の tools/ が、pyn の CLI と同じ約束で標準出力を書くことの検証。

tools/ は pyn の CLI を通らないので、t-19 の「標準出力は常に UTF-8」が
届いていなかった。Windows でパイプを受けると日本語のメッセージがロケール既定
(cp932)で出て、UTF-8 として読む側(エージェント・CI のログ)では化けていた。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

#: (モジュール, 引数, 成功時の標準出力に含まれる日本語)
TOOLS = (
    ("tools.handover_to_pyn", ["--check"], "追従しています"),
    ("tools.build_docs", ["--check"], "追従しています"),
    ("tools.build_schema", ["--check"], "追従しています"),
    ("tools.check_ids", [], "保たれています"),
)


@pytest.mark.parametrize("module,args,expected", TOOLS, ids=[t[0] for t in TOOLS])
def test_tool_stdout_is_utf8_lf_even_on_cp932_pipe(module, args, expected):
    """PYTHONIOENCODING=cp932 は、Windows でパイプを受けた状況をどの OS でも再現する。"""
    proc = subprocess.run(
        [sys.executable, "-m", module, *args],
        cwd=ROOT, capture_output=True,
        env={**os.environ, "PYTHONIOENCODING": "cp932"},
    )
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    out = proc.stdout.decode("utf-8")  # cp932 のままなら、ここで UnicodeDecodeError
    assert expected in out
    assert b"\r" not in proc.stdout
